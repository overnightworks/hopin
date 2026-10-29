import json
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Expect, Page, WebSocketRoute

from hopin.config.constants import REPLAY_LOG_LIMIT, RESUME_WINDOW_SECONDS, ErrorReason, SignalType
from tests.e2e.support.calls import join_by_link, start_call
from tests.e2e.support.media import (
    AFTER_PENDING_REJECTIONS_ARE_REPORTED,
    COUNT_OFFERS,
    EVERY_VIDEO_CAP_IS,
    FIVE_CAPS_PER_CONNECTION,
    FOUR_CAPS_PER_CONNECTION,
    MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    OFFER_COUNT,
    ONE_CAP_PER_CONNECTION,
    OPEN_PEER_COUNT_REACHED,
    OTHER_CAMERA_OFF,
    OTHER_TILE,
    RECORD_PEER_CONNECTIONS,
    REFUSE_THE_CAMERA,
    THREE_CAPS_PER_CONNECTION,
    TWO_CAPS_PER_CONNECTION,
    VIDEO_TRANSCEIVERS_PER_CONNECTION,
    inbound_per_connection,
    poll_until,
    turn_the_camera_on,
    wait_for_frames_to_advance,
    wait_for_peer_count,
    wait_for_the_picture,
)
from tests.e2e.support.signaling import RECONNECT_DELAYS_MS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

pytestmark = pytest.mark.e2e

expect = Expect()
expect.set_options(timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

CLOCK_PAUSE_LEAD = timedelta(seconds=5)
QUALITY_POLL_INTERVAL_MS = 3000
HEARD_CHAT = "#chatMessages .chat-msg.other"
LEAVE_AND_JOIN_NEWS = {SignalType.PARTICIPANT_LEFT, SignalType.PARTICIPANT_JOINED}
HOST_PRESENCE_NEWS = {SignalType.HOST_AWAY, SignalType.HOST_BACK}
PUMP_DEADLINE_SECONDS = 30
CUT_VIDEO_DEADLINE_SECONDS = RESUME_WINDOW_SECONDS // 2
UNLIMITED_DROPS = 1_000_000
SIX_PEOPLE = 6
CUTS_IN_A_ROW = 3
FORCE_ICE_FAULTS = """
window.forcedStates = new WeakMap();
window.restartOfferCount = 0;
const peerConnectionPrototype = RTCPeerConnection.prototype;
for (const property of ["iceConnectionState", "connectionState"]) {
    const nativeGetter = Object.getOwnPropertyDescriptor(peerConnectionPrototype, property).get;
    Object.defineProperty(peerConnectionPrototype, property, {
        configurable: true,
        get() { return window.forcedStates.get(this) ?? nativeGetter.call(this); },
    });
}
const nativeCreateOffer = peerConnectionPrototype.createOffer;
peerConnectionPrototype.createOffer = function (options) {
    if (options?.iceRestart) window.restartOfferCount += 1;
    return nativeCreateOffer.call(this, options);
};
window.localDescriptionCount = 0;
const nativeSetLocalDescription = peerConnectionPrototype.setLocalDescription;
peerConnectionPrototype.setLocalDescription = function (description) {
    return nativeSetLocalDescription.call(this, description).then(() => { window.localDescriptionCount += 1; });
};
const nativeSetRemoteDescription = peerConnectionPrototype.setRemoteDescription;
peerConnectionPrototype.setRemoteDescription = function (description) {
    if (description.type !== "answer") return nativeSetRemoteDescription.call(this, description);
    if (window.rejectNextAnswerApplication) {
        window.rejectNextAnswerApplication = false;
        return Promise.reject(new Error(window.injectedRejectionMessage));
    }
    window.forcedStates.delete(this);
    const gate = window.answerApplicationGate ?? Promise.resolve();
    return gate.then(() => nativeSetRemoteDescription.call(this, description));
};
window.answerCreationsStarted = 0;
window.answerCreationsSettled = 0;
const nativeCreateAnswer = peerConnectionPrototype.createAnswer;
peerConnectionPrototype.createAnswer = function (options) {
    window.answerCreationsStarted += 1;
    const created = nativeCreateAnswer.call(this, options);
    const heldUntilReleased = window.answerCreationGate ? window.answerCreationGate.then(() => created) : created;
    return heldUntilReleased.finally(() => { window.answerCreationsSettled += 1; });
};
window.forceFirstConnectionInto = (state) => {
    const connection = window.recordedConnections[0];
    if (state) window.forcedStates.set(connection, state);
    else window.forcedStates.delete(connection);
    connection.dispatchEvent(new Event("iceconnectionstatechange"));
    connection.dispatchEvent(new Event("connectionstatechange"));
};
window.localUfrags = async (connection) => {
    const stats = await connection.getStats();
    const transport = [...stats.values()].find((report) => report.type === "transport");
    const description = connection.localDescription.sdp;
    return { statsUfrag: transport?.iceLocalUsernameFragment, sdpUfrag: /a=ice-ufrag:(\\S+)/.exec(description)[1] };
};
"""
FORCE_STATE = "(state) => window.forceFirstConnectionInto(state)"
CLEAR_FORCED_STATE = "() => window.forceFirstConnectionInto(null)"
FIRST_CONNECTION_IS_CONNECTED = """
() => ["connected", "completed"].includes(window.recordedConnections[0]?.iceConnectionState)
"""
ANSWER_APPLIED = '() => window.recordedConnections[0].signalingState === "stable"'
AWAITING_ANSWER = '() => window.recordedConnections[0].signalingState === "have-local-offer"'
RESTART_OFFER_COUNT = "() => window.restartOfferCount"
HOLD_ANSWER_CREATION = """
() => {
    window.answerCreationGate = new Promise((release) => { window.releaseAnswerCreation = release; });
}
"""
RELEASE_ANSWER_CREATION = "() => window.releaseAnswerCreation()"
HOLD_ANSWER_APPLICATION = """
() => {
    window.answerApplicationGate = new Promise((release) => { window.releaseAnswerApplication = release; });
}
"""
RELEASE_ANSWER_APPLICATION = "() => window.releaseAnswerApplication()"
REJECT_NEXT_ANSWER_APPLICATION = """
(message) => {
    window.injectedRejectionMessage = message;
    window.rejectNextAnswerApplication = true;
}
"""
INJECTED_REJECTION = "answer application rejected on purpose"
ANSWER_CREATIONS_COUNTED = "() => [window.answerCreationsStarted, window.answerCreationsSettled]"
ANSWER_CREATION_STARTED = "(before) => window.answerCreationsStarted > before"
ANSWER_CREATION_SETTLED = "(before) => window.answerCreationsSettled > before"
LOCAL_DESCRIPTIONS_SET = "(count) => window.localDescriptionCount >= count"
STATUS_IS = '(text) => document.getElementById("status").textContent === text'
IN_CALL = "In call"
CONNECTION_COUNT_AND_OPEN = """
() => window.recordedConnections.length === 1 && window.recordedConnections[0].signalingState !== "closed"
"""
A_NEW_CONNECTION_IS_CONNECTED = """
() => window.recordedConnections.slice(1).some(
    (connection) => ["connected", "completed"].includes(connection.iceConnectionState)
)
"""
FIRST_CONNECTION_IS_CLOSED = '() => window.recordedConnections[0].signalingState === "closed"'
SEND = 'async (frame) => (await import("/static/js/signaling.js")).send(frame)'
SENT_LOG_REPORTS = """
async (frameTypesPerCase) => {
    const { SentLog } = await import("/static/js/replay.js");
    return frameTypesPerCase.map((frameTypes) => {
        const log = new SentLog();
        const seqs = frameTypes.map((type) => log.append({ type, target: "peer" }).seq);
        return [seqs, log.unacknowledged().map((frame) => frame.seq), log.floor];
    });
}
"""
EVERY_CASE_OF = """
async ([module, exportName, cases]) => {
    const rule = (await import(`/static/js/${module}.js`))[exportName];
    return cases.map((argument) => rule(argument));
}
"""
RESTART_DELAY_BY_ATTEMPT = {0: 2000, 1: 4000, 2: 8000, 3: 15000, 4: 15000, 5: 15000}
QUALITY_BY_ROUND_TRIP_SECONDS = {0.05: "good", 0.299: "good", 0.3: "fair", 0.499: "fair", 0.5: "poor", 2: "poor"}
VIDEO_CAP_BY_REMOTE_COUNT = dict(
    [
        ONE_CAP_PER_CONNECTION,
        TWO_CAPS_PER_CONNECTION,
        THREE_CAPS_PER_CONNECTION,
        FOUR_CAPS_PER_CONNECTION,
        FIVE_CAPS_PER_CONNECTION,
    ]
)
SDP_UFRAG = "async () => (await window.localUfrags(window.recordedConnections[0])).sdpUfrag"
UFRAG_ROTATED_AND_IN_STATS = """
async (previousUfrag) => {
    const { statsUfrag, sdpUfrag } = await window.localUfrags(window.recordedConnections[0]);
    return sdpUfrag !== previousUfrag && statsUfrag === sdpUfrag;
}
"""
REMOTE_UFRAG = "() => /a=ice-ufrag:(\\S+)/.exec(window.recordedConnections[0].remoteDescription.sdp)[1]"
ICE_DISCONNECTED_GRACE_MS = 2000
RECORD_SOCKETS = """
window.openedSockets = [];
const PageWebSocket = window.WebSocket;
window.WebSocket = class extends PageWebSocket {
    constructor(...socketArguments) {
        super(...socketArguments);
        window.openedSockets.push(this);
    }
};
"""
LATE_EVENTS_OF_THE_FIRST_SOCKET = """
() => {
    const [first] = window.openedSockets;
    first.dispatchEvent(new Event("error"));
    first.dispatchEvent(new Event("close"));
}
"""
# Counts every write to the status line, so a scenario can tell a rewrite of the same text from none.
COUNT_STATUS_WRITES = """
window.statusWrites = 0;
new MutationObserver((records) => {
    for (const { target } of records) if (target.id === "status") window.statusWrites++;
}).observe(document, { childList: true, subtree: true });
"""
STATUS_WRITES = "() => window.statusWrites"
STATUS_WRITTEN_AGAIN = "(before) => window.statusWrites > before"
BLANK_QUALITY_BARS = """
() => document.querySelectorAll(".connection-quality").forEach((indicator) => {
    delete indicator.dataset.quality;
})
"""
QUALITY_BARS_PAINTED = '() => document.querySelector(".connection-quality[data-quality]") !== null'
VIDEO_TARGET_BITRATES = """
async () => {
    const open = window.recordedConnections.filter((connection) => connection.signalingState !== "closed");
    const reports = (await Promise.all(open.map((connection) => connection.getStats())))
        .flatMap((stats) => [...stats.values()])
        .filter((report) => report.type === "outbound-rtp" && report.kind === "video");
    return reports.map((report) => report.targetBitrate);
}
"""


@dataclass
class SignalingTap:
    """Sits on one person's signaling socket, records what it carries and can damage the connection."""

    urls: list[str] = field(default_factory=list)
    upstream: list[dict[str, Any]] = field(default_factory=list)
    downstream: list[dict[str, Any]] = field(default_factory=list)
    swallow_upstream: int = 0
    offers_to_drop: int = 0
    blackhole_downstream: bool = False
    refused_connections: int = 0
    corrupt_resume_token: bool = False
    deliver_chats_twice: bool = False
    hold_resumed: bool = False
    held_resumed: list[str | bytes] = field(default_factory=list)
    sent_before_resumed_released: int = 0
    _sides: tuple[WebSocketRoute, WebSocketRoute] | None = None

    def relay(self, browser_side: WebSocketRoute) -> None:
        self.urls.append(browser_side.url)
        refusing = self.refused_connections > 0
        self.refused_connections -= int(refusing)
        server_side = browser_side.connect_to_server()
        self._sides = (browser_side, server_side)
        browser_side.on_message(lambda frame: self._to_server(server_side, frame, refusing=refusing))
        server_side.on_message(lambda frame: self._to_browser(browser_side, frame))

    def cut(self) -> None:
        assert self._sides is not None
        browser_side, server_side = self._sides
        browser_side.close()
        server_side.close()

    def release_held_resumed(self) -> None:
        assert self._sides is not None
        self.sent_before_resumed_released = len(self.upstream)
        for frame in self.held_resumed:
            self._sides[0].send(frame)
        self.held_resumed.clear()

    def _to_server(self, server_side: WebSocketRoute, frame: str | bytes, *, refusing: bool) -> None:
        message = json.loads(frame)
        self.upstream.append(message)
        if refusing:
            self.cut()
        elif self.swallow_upstream > 0:
            self.swallow_upstream -= 1
        elif self.offers_to_drop > 0 and message["type"] == SignalType.OFFER:
            self.offers_to_drop -= 1
        else:
            if self.corrupt_resume_token and message["type"] == SignalType.RESUME:
                frame = json.dumps({**message, "token": message["token"] + "x"})
            server_side.send(frame)

    def _to_browser(self, browser_side: WebSocketRoute, frame: str | bytes) -> None:
        message = json.loads(frame)
        self.downstream.append(message)
        if self.blackhole_downstream:
            return
        if self.hold_resumed and message["type"] == SignalType.RESUMED:
            self.held_resumed.append(frame)
        else:
            browser_side.send(frame)
            if self.deliver_chats_twice and message["type"] == SignalType.CHAT:
                browser_side.send(frame)

    def types(self, direction: list[dict[str, Any]]) -> list[str]:
        return [message["type"] for message in direction]


class Side(Enum):
    HOST = "host"
    GUEST = "guest"


@dataclass
class ConnectedPair:
    host: Page
    guest: Page
    host_tap: SignalingTap
    guest_tap: SignalingTap
    invite_link: str
    page_errors: list[str]

    def force_failed_on(self, person: Page) -> None:
        person.evaluate(FORCE_STATE, "failed")

    def tap_of(self, person: Page) -> SignalingTap:
        return self.host_tap if person is self.host else self.guest_tap

    def person_on(self, side: Side) -> Page:
        return self.host if side is Side.HOST else self.guest

    def other_than(self, person: Page) -> Page:
        return self.guest if person is self.host else self.host

    def cut(self, person: Page) -> None:
        self.tap_of(person).cut()
        wait_for_status(person, _reconnecting_status(RECONNECT_DELAYS_MS[0]))

    def reconnect(self, person: Page) -> None:
        person.clock.run_for(RECONNECT_DELAYS_MS[0])

    def resume(self, person: Page) -> None:
        self.reconnect(person)
        wait_for_status(person, IN_CALL)

    @contextmanager
    def socket_down(self, person: Page) -> Iterator[None]:
        self.cut(person)
        yield
        self.resume(person)


def _reconnecting_status(delay_ms: int) -> str:
    return f"Reconnecting in {delay_ms // 1000}s..."


def _wait_for(person: Page, condition: str, arg: Any = None) -> None:
    person.wait_for_function(condition, arg=arg, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


def wait_for_status(person: Page, text: str) -> None:
    _wait_for(person, STATUS_IS, text)


def pump_until(
    person: Page,
    condition: Callable[[], bool],
    deadline_seconds: int = PUMP_DEADLINE_SECONDS,
    failure: str = "the awaited signaling traffic never happened",
) -> None:
    poll_until(person, condition, bool, failure, deadline_seconds)


def _restart_offers(person: Page) -> int:
    return int(person.evaluate(RESTART_OFFER_COUNT))


def _sdp_ufrag(person: Page) -> str:
    return str(person.evaluate(SDP_UFRAG))


def _wait_for_new_ufrag_in_stats(person: Page, previous_ufrag: str) -> None:
    pump_until(person, lambda: bool(person.evaluate(UFRAG_ROTATED_AND_IN_STATS, previous_ufrag)))


@contextmanager
def _expect_to_reconnect_on_a_new_ufrag(*people: Page) -> Iterator[None]:
    ufrags_before = [_sdp_ufrag(person) for person in people]
    yield
    for person, ufrag_before in zip(people, ufrags_before, strict=True):
        _wait_for_new_ufrag_in_stats(person, ufrag_before)
        _wait_for(person, FIRST_CONNECTION_IS_CONNECTED)


@pytest.fixture
def connect_pair(open_person: Callable[..., Page], base_url: str) -> Iterator[Callable[..., ConnectedPair]]:
    page_errors: list[str] = []

    def connect(without_camera: Side | None = None) -> ConnectedPair:
        host, guest = open_person(), open_person()
        host_tap, guest_tap = SignalingTap(), SignalingTap()
        for person, tap in ((host, host_tap), (guest, guest_tap)):
            person.route_web_socket("**/ws", tap.relay)
            person.on("pageerror", lambda error: page_errors.append(str(error)))
            person.clock.install()
            person.add_init_script(RECORD_PEER_CONNECTIONS)
            person.add_init_script(FORCE_ICE_FAULTS)
            person.add_init_script(COUNT_OFFERS)
            person.add_init_script(RECORD_SOCKETS)
            person.add_init_script(COUNT_STATUS_WRITES)

        guest.goto("about:blank")
        if without_camera is Side.GUEST:
            guest.add_init_script(REFUSE_THE_CAMERA)
        invite_link = start_call(host, base_url, with_camera=without_camera is not Side.HOST)
        join_by_link(guest, invite_link)
        for person in (host, guest):
            _wait_for(person, FIRST_CONNECTION_IS_CONNECTED)
            person.clock.pause_at(datetime.now(tz=UTC) + CLOCK_PAUSE_LEAD)
        return ConnectedPair(host, guest, host_tap, guest_tap, invite_link, page_errors)

    yield connect
    assert page_errors == []


@pytest.fixture
def connected_pair(connect_pair: Callable[..., ConnectedPair]) -> ConnectedPair:
    return connect_pair()


@pytest.fixture
def module_page(open_person: Callable[..., Page], base_url: str) -> Page:
    page = open_person()
    page.goto(f"{base_url}/")
    return page


def test_forced_failure_on_the_joiner_restarts_ice_once_and_both_sides_take_the_new_ufrag(
    connected_pair: ConnectedPair,
) -> None:
    guest = connected_pair.guest

    with _expect_to_reconnect_on_a_new_ufrag(guest, connected_pair.host):
        connected_pair.force_failed_on(guest)
        assert _restart_offers(guest) == 1

    guest.clock.run_for(RESTART_DELAY_BY_ATTEMPT[0])
    assert _restart_offers(guest) == 1


def test_disconnected_joiner_restarts_after_the_grace_and_not_before(connected_pair: ConnectedPair) -> None:
    guest = connected_pair.guest
    guest.evaluate(FORCE_STATE, "disconnected")
    guest.clock.run_for(ICE_DISCONNECTED_GRACE_MS - 1)
    assert _restart_offers(guest) == 0
    guest.clock.run_for(1)

    assert _restart_offers(guest) == 1


def test_disconnected_joiner_that_recovers_within_the_grace_does_not_restart(connected_pair: ConnectedPair) -> None:
    guest = connected_pair.guest
    guest.evaluate(FORCE_STATE, "disconnected")
    guest.clock.run_for(ICE_DISCONNECTED_GRACE_MS // 2)
    guest.evaluate(CLEAR_FORCED_STATE)
    guest.clock.run_for(ICE_DISCONNECTED_GRACE_MS)

    assert _restart_offers(guest) == 0


def test_lost_restart_offer_is_retried_from_have_local_offer_and_the_retry_schedule_resets(
    connected_pair: ConnectedPair,
) -> None:
    guest = connected_pair.guest
    first_delay = RESTART_DELAY_BY_ATTEMPT[0]
    connected_pair.guest_tap.offers_to_drop = 1

    with _expect_to_reconnect_on_a_new_ufrag(guest, connected_pair.host):
        connected_pair.force_failed_on(guest)
        _wait_for(guest, AWAITING_ANSWER)
        guest.clock.run_for(first_delay)
        assert _restart_offers(guest) == 2

    guest.clock.run_for(RESTART_DELAY_BY_ATTEMPT[1])
    assert _restart_offers(guest) == 2
    connected_pair.guest_tap.offers_to_drop = 1
    connected_pair.force_failed_on(guest)
    assert _restart_offers(guest) == 3
    guest.clock.run_for(first_delay)
    assert _restart_offers(guest) == 4


def test_forced_failure_on_the_answerer_never_sends_a_restart_offer(connected_pair: ConnectedPair) -> None:
    host = connected_pair.host

    connected_pair.force_failed_on(host)
    host.clock.run_for(RESTART_DELAY_BY_ATTEMPT[0] + RESTART_DELAY_BY_ATTEMPT[1])

    assert _restart_offers(host) == 0


def test_quality_indicator_keeps_polling_through_a_failure_and_after_the_recovery(
    connected_pair: ConnectedPair,
) -> None:
    guest = connected_pair.guest
    connected_pair.guest_tap.offers_to_drop = UNLIMITED_DROPS
    guest.clock.run_for(QUALITY_POLL_INTERVAL_MS)
    _wait_for(guest, QUALITY_BARS_PAINTED)

    connected_pair.force_failed_on(guest)
    guest.clock.run_for(QUALITY_POLL_INTERVAL_MS)
    connected_pair.guest_tap.offers_to_drop = 0
    guest.clock.run_for(RESTART_DELAY_BY_ATTEMPT[1])
    _wait_for(guest, FIRST_CONNECTION_IS_CONNECTED)
    guest.evaluate(BLANK_QUALITY_BARS)
    guest.clock.run_for(QUALITY_POLL_INTERVAL_MS)

    _wait_for(guest, QUALITY_BARS_PAINTED)


@pytest.mark.parametrize(
    ("module", "export_name", "value_by_case"),
    [
        ("recovery", "restartDelay", RESTART_DELAY_BY_ATTEMPT),
        ("bandwidth", "videoBitrateCap", VIDEO_CAP_BY_REMOTE_COUNT),
        ("quality", "qualityOfRoundTrip", QUALITY_BY_ROUND_TRIP_SECONDS),
    ],
    ids=[
        "restart_delay_follows_the_schedule_and_repeats_the_last_delay",
        "video_bitrate_cap_divides_the_budget_among_the_remote_peers",
        "quality_of_round_trip_is_good_below_300_ms_fair_below_500_ms_and_poor_above",
    ],
)
def test_browser_rule_gives_every_case_its_expected_value(
    module_page: Page, module: str, export_name: str, value_by_case: dict[Any, Any]
) -> None:
    values = module_page.evaluate(EVERY_CASE_OF, [module, export_name, list(value_by_case)])

    assert dict(zip(value_by_case, values, strict=True)) == value_by_case


def _wait_for_video_caps(people: list[Page], peer_count: int, expected_cap: int) -> None:
    for person in people:
        wait_for_peer_count(person, peer_count)
        _wait_for(person, EVERY_VIDEO_CAP_IS, [peer_count, expected_cap])


def _wait_for_video_targets_at_most(person: Page, peer_count: int, ceiling: int) -> None:
    poll_until(
        person,
        lambda: person.evaluate(VIDEO_TARGET_BITRATES),
        lambda targets: len(targets) == peer_count and None not in targets and max(targets) <= ceiling,
        f"the video targets did not fall to {ceiling} for {peer_count} connections",
    )


def test_six_person_call_caps_every_video_upload_by_call_size(open_person: Callable[..., Page], base_url: str) -> None:
    people = [open_person() for _ in range(SIX_PEOPLE)]
    for person in people:
        person.add_init_script(RECORD_PEER_CONNECTIONS)
    invite_link = start_call(people[0], base_url)
    for joined, person in enumerate(people[1:], start=2):
        join_by_link(person, invite_link)
        for earlier in people[:joined]:
            wait_for_peer_count(earlier, joined - 1)
        if joined == 3:
            _wait_for_video_caps(people[:joined], *TWO_CAPS_PER_CONNECTION)

    _wait_for_video_caps(people, *FIVE_CAPS_PER_CONNECTION)
    for person in people:
        _wait_for_video_targets_at_most(person, *FIVE_CAPS_PER_CONNECTION)

    people[-1].close()
    _wait_for_video_caps(people[:-1], *FOUR_CAPS_PER_CONNECTION)


def _inbound_video_bytes(person: Page) -> int:
    return int(sum(inbound_per_connection(person, "video", "bytesReceived")))


def _wait_for_more_video_bytes_than(person: Page, baseline: int, deadline_seconds: int = PUMP_DEADLINE_SECONDS) -> int:
    pump_until(
        person,
        lambda: _inbound_video_bytes(person) > baseline,
        deadline_seconds,
        "no new video bytes arrived",
    )
    return _inbound_video_bytes(person)


def _frames_of(tap: SignalingTap, direction: str, frame_type: str, since: int = 0) -> list[dict[str, Any]]:
    return [message for message in getattr(tap, direction)[since:] if message["type"] == frame_type]


def _say_in_chat(person: Page, text: str) -> None:
    if not person.locator("#chatPanel.visible").count():
        person.click("#chatBtn")
    person.fill("#chatInput", text)
    person.click("#chatSend")


def _heard_since(tap: SignalingTap, since: int) -> set[str]:
    return set(tap.types(tap.downstream[since:]))


@pytest.mark.parametrize("cut_side", [Side.GUEST, Side.HOST], ids=["guest_cut", "host_cut"])
def test_cut_signaling_socket_resumes_the_seat_while_the_video_keeps_flowing_and_nobody_hears_a_leave_or_join(
    connected_pair: ConnectedPair, cut_side: Side
) -> None:
    cut_person = connected_pair.person_on(cut_side)
    cut_tap = connected_pair.tap_of(cut_person)
    observer_tap = connected_pair.tap_of(connected_pair.other_than(cut_person))
    heard_before = len(observer_tap.downstream)
    bytes_before = _inbound_video_bytes(cut_person)

    with connected_pair.socket_down(cut_person):
        bytes_during_the_cut = _wait_for_more_video_bytes_than(cut_person, bytes_before, CUT_VIDEO_DEADLINE_SECONDS)
    _wait_for_more_video_bytes_than(cut_person, bytes_during_the_cut)

    for person in (connected_pair.host, connected_pair.guest):
        assert person.evaluate(CONNECTION_COUNT_AND_OPEN)
    assert LEAVE_AND_JOIN_NEWS.union(HOST_PRESENCE_NEWS).isdisjoint(_heard_since(observer_tap, heard_before))
    sent = cut_tap.types(cut_tap.upstream)
    assert (sent.count(SignalType.JOIN), sent.count(SignalType.RESUME)) == (1, 1)


def test_restart_answer_swallowed_at_the_cut_is_resent_after_the_resume_and_both_sides_take_the_new_ufrag(
    connected_pair: ConnectedPair,
) -> None:
    guest, host, host_tap = connected_pair.guest, connected_pair.host, connected_pair.host_tap
    answers_before = len(_frames_of(connected_pair.guest_tap, "downstream", SignalType.ANSWER))
    upstream_before = len(host_tap.upstream)
    host_tap.swallow_upstream = UNLIMITED_DROPS

    with _expect_to_reconnect_on_a_new_ufrag(guest, host):
        connected_pair.force_failed_on(guest)
        pump_until(host, lambda: bool(_frames_of(host_tap, "upstream", SignalType.ANSWER, since=upstream_before)))
        with connected_pair.socket_down(host):
            host_tap.swallow_upstream = 0

    _say_in_chat(host, "sent after the answer")
    expect(guest.locator(HEARD_CHAT)).to_have_count(1)
    assert len(_frames_of(connected_pair.guest_tap, "downstream", SignalType.ANSWER)) == answers_before + 1


def test_chat_swallowed_at_the_cut_reaches_the_other_side_exactly_once_after_the_resume(
    connected_pair: ConnectedPair,
) -> None:
    guest = connected_pair.guest
    connected_pair.guest_tap.swallow_upstream = 1

    _say_in_chat(guest, "lost in the cut")
    pump_until(guest, lambda: connected_pair.guest_tap.swallow_upstream == 0)
    connected_pair.cut(guest)
    connected_pair.resume(guest)

    expect(connected_pair.host.locator(HEARD_CHAT)).to_have_text([re.compile("lost in the cut$")])
    assert len(_frames_of(connected_pair.host_tap, "downstream", SignalType.CHAT)) == 1


def test_chat_typed_while_the_socket_is_down_is_written_in_order_after_the_resume(
    connected_pair: ConnectedPair,
) -> None:
    guest, guest_tap = connected_pair.guest, connected_pair.guest_tap

    with connected_pair.socket_down(guest):
        _say_in_chat(guest, "first")
        _say_in_chat(guest, "second")

    expect(connected_pair.host.locator(HEARD_CHAT)).to_have_count(2)
    last_resume = max(index for index, message in enumerate(guest_tap.upstream) if message["type"] == SignalType.RESUME)
    chats_after_it = _frames_of(guest_tap, "upstream", SignalType.CHAT, since=last_resume + 1)
    assert [chat["message"] for chat in chats_after_it] == ["first", "second"]


def _retry_the_restart_while_the_first_reconnect_is_refused(person: Page, retry_made: Callable[[], bool]) -> None:
    first_delay, second_delay = RECONNECT_DELAYS_MS[:2]
    until_the_retry = RESTART_DELAY_BY_ATTEMPT[0] - first_delay
    person.clock.run_for(first_delay)
    wait_for_status(person, _reconnecting_status(second_delay))
    person.clock.run_for(until_the_retry)
    pump_until(person, retry_made)
    person.clock.run_for(second_delay - until_the_retry)
    wait_for_status(person, IN_CALL)


def test_two_restart_offers_made_while_the_socket_is_down_reach_the_host_as_one(
    connected_pair: ConnectedPair,
) -> None:
    guest, host = connected_pair.guest, connected_pair.host
    offers_before = len(_frames_of(connected_pair.host_tap, "downstream", SignalType.OFFER))
    descriptions_before = int(guest.evaluate("() => window.localDescriptionCount"))
    connected_pair.guest_tap.refused_connections = 1
    connected_pair.cut(guest)

    connected_pair.force_failed_on(guest)
    _wait_for(guest, LOCAL_DESCRIPTIONS_SET, descriptions_before + 1)
    _retry_the_restart_while_the_first_reconnect_is_refused(
        guest, lambda: bool(guest.evaluate(LOCAL_DESCRIPTIONS_SET, descriptions_before + 2))
    )
    _say_in_chat(guest, "sent after both offers")
    expect(host.locator(HEARD_CHAT)).to_have_count(1)

    assert _restart_offers(guest) == 2
    assert len(_frames_of(connected_pair.host_tap, "downstream", SignalType.OFFER)) == offers_before + 1
    assert host.evaluate(CONNECTION_COUNT_AND_OPEN)


def test_answer_arriving_while_an_earlier_answer_is_being_applied_waits_for_it_and_raises_no_page_error(
    connected_pair: ConnectedPair,
) -> None:
    guest, host = connected_pair.guest, connected_pair.host
    guest_tap, host_tap = connected_pair.guest_tap, connected_pair.host_tap
    answers_before = len(_frames_of(guest_tap, "downstream", SignalType.ANSWER))
    answers_sent = len(_frames_of(host_tap, "upstream", SignalType.ANSWER))
    guest_tap.blackhole_downstream = True
    guest_tap.refused_connections = 1
    guest.evaluate(HOLD_ANSWER_APPLICATION)

    with _expect_to_reconnect_on_a_new_ufrag(guest, host):
        connected_pair.force_failed_on(guest)
        pump_until(guest, lambda: len(_frames_of(guest_tap, "downstream", SignalType.ANSWER)) > answers_before)
        connected_pair.cut(guest)
        guest_tap.blackhole_downstream = False
        _retry_the_restart_while_the_first_reconnect_is_refused(guest, lambda: _restart_offers(guest) == 2)
        pump_until(host, lambda: len(_frames_of(host_tap, "upstream", SignalType.ANSWER)) == answers_sent + 2)
        _say_in_chat(host, "sent after both answers")
        expect(guest.locator(HEARD_CHAT)).to_have_count(1)
        guest.evaluate(RELEASE_ANSWER_APPLICATION)
        guest.evaluate(AFTER_PENDING_REJECTIONS_ARE_REPORTED)


def test_rejected_answer_application_is_reported_and_the_next_answer_is_still_applied(
    connected_pair: ConnectedPair,
) -> None:
    guest = connected_pair.guest
    guest.evaluate(REJECT_NEXT_ANSWER_APPLICATION, INJECTED_REJECTION)

    with _expect_to_reconnect_on_a_new_ufrag(guest, connected_pair.host):
        connected_pair.force_failed_on(guest)
        pump_until(guest, lambda: bool(connected_pair.page_errors))
        guest.clock.run_for(RESTART_DELAY_BY_ATTEMPT[0])
        _wait_for(guest, ANSWER_APPLIED)
        assert [INJECTED_REJECTION in error for error in connected_pair.page_errors] == [True]
        connected_pair.page_errors.pop()


def test_every_cut_in_a_row_resumes_with_the_first_delay_because_each_resume_resets_the_attempts(
    connected_pair: ConnectedPair,
) -> None:
    guest = connected_pair.guest

    for _ in range(CUTS_IN_A_ROW):
        connected_pair.cut(guest)
        connected_pair.resume(guest)

    connected_pair.cut(guest)


def test_server_frame_delivered_twice_is_dispatched_once(connected_pair: ConnectedPair) -> None:
    guest, host = connected_pair.guest, connected_pair.host
    connected_pair.guest_tap.deliver_chats_twice = True

    _say_in_chat(host, "once")
    _say_in_chat(host, "marker")

    expect(guest.locator(HEARD_CHAT)).to_have_text([re.compile("once$"), re.compile("marker$")])


def test_events_of_a_replaced_socket_are_ignored(connected_pair: ConnectedPair) -> None:
    guest = connected_pair.guest
    connected_pair.cut(guest)
    connected_pair.resume(guest)

    guest.evaluate(LATE_EVENTS_OF_THE_FIRST_SOCKET)
    guest.clock.run_for(RECONNECT_DELAYS_MS[0])
    _say_in_chat(guest, "still connected")

    expect(connected_pair.host.locator(HEARD_CHAT)).to_have_count(1)
    assert len(connected_pair.guest_tap.urls) == 2
    wait_for_status(guest, IN_CALL)


def test_chat_typed_while_the_socket_is_open_but_not_yet_resumed_waits_for_the_resumed_frame(
    connected_pair: ConnectedPair,
) -> None:
    guest, host, tap = connected_pair.guest, connected_pair.host, connected_pair.guest_tap
    tap.hold_resumed = True
    connected_pair.cut(guest)
    connected_pair.reconnect(guest)
    pump_until(guest, lambda: bool(tap.held_resumed))

    _say_in_chat(guest, "typed while resuming")
    tap.release_held_resumed()

    expect(host.locator(HEARD_CHAT)).to_have_count(1)
    assert SignalType.CHAT not in tap.types(tap.upstream[: tap.sent_before_resumed_released])
    assert len(_frames_of(connected_pair.host_tap, "downstream", SignalType.CHAT)) == 1


def test_refused_resume_falls_back_to_a_fresh_join_with_new_peer_connections(
    connected_pair: ConnectedPair,
) -> None:
    guest = connected_pair.guest
    connected_pair.guest_tap.corrupt_resume_token = True

    connected_pair.cut(guest)
    connected_pair.reconnect(guest)
    _wait_for(guest, A_NEW_CONNECTION_IS_CONNECTED)

    assert guest.evaluate(FIRST_CONNECTION_IS_CLOSED)
    sent = connected_pair.guest_tap.types(connected_pair.guest_tap.upstream)
    assert (sent.count(SignalType.JOIN), sent.count(SignalType.RESUME)) == (2, 1)
    refusals = _frames_of(connected_pair.guest_tap, "downstream", SignalType.ERROR)
    assert [refusal["reason"] for refusal in refusals] == [ErrorReason.RESUME_REFUSED]


def test_reconnects_that_are_all_refused_end_in_connection_lost_after_the_whole_window(
    connected_pair: ConnectedPair,
) -> None:
    guest, tap = connected_pair.guest, connected_pair.guest_tap
    tap.refused_connections = len(RECONNECT_DELAYS_MS)
    connected_pair.cut(guest)

    for attempt, delay in enumerate(RECONNECT_DELAYS_MS, start=1):
        writes_before = guest.evaluate(STATUS_WRITES)
        guest.clock.run_for(delay)
        _wait_for(guest, STATUS_WRITTEN_AGAIN, writes_before)
        if attempt < len(RECONNECT_DELAYS_MS):
            wait_for_status(guest, _reconnecting_status(RECONNECT_DELAYS_MS[attempt]))
    wait_for_status(guest, "Connection lost")
    upstream_before = len(tap.upstream)
    _say_in_chat(guest, "nobody hears this")

    assert len(tap.urls) == len(RECONNECT_DELAYS_MS) + 1
    assert len(tap.upstream) == upstream_before
    window_ms = RESUME_WINDOW_SECONDS * 1000
    assert window_ms - RECONNECT_DELAYS_MS[-1] <= sum(RECONNECT_DELAYS_MS) < window_ms


def test_sending_without_a_session_throws(module_page: Page) -> None:
    with pytest.raises(PlaywrightError, match="No signaling session"):
        module_page.evaluate(SEND, {"type": SignalType.CHAT, "message": "no session"})


def test_no_signaling_url_carries_a_resume_token(connected_pair: ConnectedPair) -> None:
    guest = connected_pair.guest
    connected_pair.cut(guest)
    connected_pair.resume(guest)

    tokens = {message["resume_token"] for message in connected_pair.guest_tap.downstream if "resume_token" in message}
    assert len(tokens) == 2
    assert not any(token in url for token in tokens for url in connected_pair.guest_tap.urls)


def test_leaving_while_a_newcomers_offer_is_being_answered_raises_no_page_error(
    connected_pair: ConnectedPair, open_person: Callable[..., Page]
) -> None:
    host = connected_pair.host
    started_before, settled_before = host.evaluate(ANSWER_CREATIONS_COUNTED)
    host.evaluate(HOLD_ANSWER_CREATION)
    newcomer = open_person()
    join_by_link(newcomer, connected_pair.invite_link)
    _wait_for(host, ANSWER_CREATION_STARTED, started_before)

    host.click("#leaveBtn")
    host.click("#leaveYes")
    host.locator("[data-screen=end]").wait_for(timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    host.evaluate(RELEASE_ANSWER_CREATION)

    _wait_for(host, ANSWER_CREATION_SETTLED, settled_before)
    host.evaluate(AFTER_PENDING_REJECTIONS_ARE_REPORTED)


def test_peer_removed_in_the_middle_of_a_restart_episode_stops_the_restart_schedule(
    connected_pair: ConnectedPair,
) -> None:
    guest, host = connected_pair.guest, connected_pair.host
    connected_pair.guest_tap.offers_to_drop = UNLIMITED_DROPS
    connected_pair.force_failed_on(guest)
    assert _restart_offers(guest) == 1

    host.click("#leaveBtn")
    host.click("#leaveYes")
    wait_for_peer_count(guest, 0)
    guest.clock.run_for(sum(RESTART_DELAY_BY_ATTEMPT.values()))

    assert _restart_offers(guest) == 1


def test_last_peer_leaving_raises_no_page_error_on_the_one_who_stays(connected_pair: ConnectedPair) -> None:
    guest, host = connected_pair.guest, connected_pair.host

    guest.click("#leaveBtn")

    wait_for_peer_count(host, 0)
    host.clock.run_for(sum(RESTART_DELAY_BY_ATTEMPT.values()))


def test_sent_log_numbers_frames_drops_what_a_newer_description_supersedes_and_keeps_the_server_log_limit(
    module_page: Page,
) -> None:
    kept_and_floor_by_frame_types = {
        (SignalType.CHAT, SignalType.CHAT): ([1, 2], 0),
        (SignalType.OFFER, SignalType.ICE_CANDIDATE, SignalType.OFFER): ([3], 0),
        (SignalType.ANSWER, SignalType.ICE_CANDIDATE, SignalType.ANSWER, SignalType.ICE_CANDIDATE): ([3, 4], 0),
        (SignalType.CHAT,) * (REPLAY_LOG_LIMIT + 3): (list(range(4, REPLAY_LOG_LIMIT + 4)), 3),
    }

    reports = module_page.evaluate(
        SENT_LOG_REPORTS, [list(frame_types) for frame_types in kept_and_floor_by_frame_types]
    )

    assert reports == [
        [list(range(1, len(frame_types) + 1)), kept, floor]
        for frame_types, (kept, floor) in kept_and_floor_by_frame_types.items()
    ]


@pytest.mark.parametrize("camera_less", [Side.GUEST, Side.HOST], ids=["joiner_turns_it_on", "answerer_turns_it_on"])
def test_camera_turned_on_mid_call_reaches_the_other_side_without_an_offer_and_survives_an_ice_restart(
    connect_pair: Callable[..., ConnectedPair], camera_less: Side
) -> None:
    pair = connect_pair(without_camera=camera_less)
    guest, watcher = pair.guest, pair.other_than(pair.person_on(camera_less))
    assert inbound_per_connection(watcher, "video", "framesDecoded") == [0]

    turn_the_camera_on(pair.person_on(camera_less))
    wait_for_the_picture(watcher)

    assert (_restart_offers(guest), guest.evaluate(OFFER_COUNT)) == (0, 1)
    with _expect_to_reconnect_on_a_new_ufrag(guest):
        pair.force_failed_on(guest)
        assert _restart_offers(guest) == 1
    wait_for_the_picture(watcher)
    for person in (pair.host, guest):
        assert person.evaluate(VIDEO_TRANSCEIVERS_PER_CONNECTION) == [1]


def test_camera_turned_on_while_the_socket_is_down_reaches_the_host_and_its_media_state_follows_the_resume(
    connect_pair: Callable[..., ConnectedPair],
) -> None:
    pair = connect_pair(without_camera=Side.GUEST)
    guest, host = pair.guest, pair.host
    heard_before = len(pair.host_tap.downstream)

    with pair.socket_down(guest):
        turn_the_camera_on(guest)
        wait_for_frames_to_advance(host)
        assert guest.evaluate(OFFER_COUNT) == 1
        expect(host.locator(OTHER_CAMERA_OFF)).to_be_visible()

    expect(host.locator(OTHER_CAMERA_OFF)).to_be_hidden()
    assert LEAVE_AND_JOIN_NEWS.isdisjoint(_heard_since(pair.host_tap, heard_before))


def _wait_for_offers_and_candidates_after_them(connected_pair: ConnectedPair, offers: int) -> None:
    host, host_tap = connected_pair.host, connected_pair.host_tap

    pump_until(host, lambda: len(_frames_of(host_tap, "downstream", SignalType.OFFER)) >= offers)
    last_offer_at = max(
        index for index, message in enumerate(host_tap.downstream) if message["type"] == SignalType.OFFER
    )
    last_offer_ufrag = re.findall(r"a=ice-ufrag:(\S+)", host_tap.downstream[last_offer_at]["sdp"]["sdp"])[0]

    def candidate_of_the_last_offer_arrived() -> bool:
        candidates = _frames_of(host_tap, "downstream", SignalType.ICE_CANDIDATE, last_offer_at)
        return any(frame["candidate"]["usernameFragment"] == last_offer_ufrag for frame in candidates)

    pump_until(host, candidate_of_the_last_offer_arrived)


def test_two_restart_offers_back_to_back_are_answered_in_arrival_order_and_the_pair_connects_again_on_the_next_retry(
    connected_pair: ConnectedPair,
) -> None:
    guest, host, host_tap = connected_pair.guest, connected_pair.host, connected_pair.host_tap
    offers_before = len(_frames_of(host_tap, "downstream", SignalType.OFFER))
    answers_before = len(_frames_of(host_tap, "upstream", SignalType.ANSWER))
    guest_before, host_before = _sdp_ufrag(guest), _sdp_ufrag(host)
    host.evaluate(HOLD_ANSWER_CREATION)

    connected_pair.force_failed_on(guest)
    guest.clock.run_for(RESTART_DELAY_BY_ATTEMPT[0])
    _wait_for_offers_and_candidates_after_them(connected_pair, offers_before + 2)
    second_offer_ufrag = _sdp_ufrag(guest)
    host.evaluate(RELEASE_ANSWER_CREATION)

    pump_until(host, lambda: len(_frames_of(host_tap, "upstream", SignalType.ANSWER)) == answers_before + 2)
    host.evaluate(AFTER_PENDING_REJECTIONS_ARE_REPORTED)
    assert second_offer_ufrag != guest_before
    assert host.evaluate(REMOTE_UFRAG) == second_offer_ufrag
    guest.clock.run_for(RESTART_DELAY_BY_ATTEMPT[1])
    _wait_for(guest, FIRST_CONNECTION_IS_CONNECTED)
    _wait_for_new_ufrag_in_stats(guest, guest_before)
    _wait_for_new_ufrag_in_stats(host, host_before)


def test_leaving_behind_a_queued_restart_offer_leaves_no_tile_and_no_connection_on_the_one_who_stays(
    connected_pair: ConnectedPair,
) -> None:
    guest, host, host_tap = connected_pair.guest, connected_pair.host, connected_pair.host_tap
    offers_before = len(_frames_of(host_tap, "downstream", SignalType.OFFER))
    started_before, settled_before = host.evaluate(ANSWER_CREATIONS_COUNTED)
    host.evaluate(HOLD_ANSWER_CREATION)
    connected_pair.force_failed_on(guest)
    _wait_for(host, ANSWER_CREATION_STARTED, started_before)
    guest.clock.run_for(RESTART_DELAY_BY_ATTEMPT[0])
    pump_until(host, lambda: len(_frames_of(host_tap, "downstream", SignalType.OFFER)) == offers_before + 2)

    guest.click("#leaveBtn")
    wait_for_peer_count(host, 0)
    host.evaluate(RELEASE_ANSWER_CREATION)

    _wait_for(host, ANSWER_CREATION_SETTLED, settled_before)
    host.evaluate(AFTER_PENDING_REJECTIONS_ARE_REPORTED)
    expect(host.locator(OTHER_TILE)).to_have_count(0)
    assert host.evaluate(OPEN_PEER_COUNT_REACHED, 0)
