import json
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Expect, Page, WebSocketRoute

from hopin.config.constants import REPLAY_LOG_LIMIT, RESUME_WINDOW_SECONDS
from tests.e2e.conftest import (
    COUNT_OFFERS,
    EVERY_VIDEO_CAP_IS,
    MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    OFFER_COUNT,
    OTHER_CAMERA_OFF,
    RECORD_PEER_CONNECTIONS,
    REFUSE_THE_CAMERA,
    inbound_per_connection,
    start_call,
    turn_the_camera_on,
    wait_for_frames_to_advance,
    wait_for_peer_count,
)

pytestmark = pytest.mark.e2e

expect = Expect()
expect.set_options(timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

CLOCK_PAUSE_LEAD = timedelta(seconds=5)
QUALITY_POLL_INTERVAL_MS = 3000
OFFER_FRAME_TYPE = "offer"
ANSWER_FRAME_TYPE = "answer"
CHAT_FRAME_TYPE = "chat"
RESUME_FRAME_TYPE = "resume"
RESUMED_FRAME_TYPE = "resumed"
RESUMED_RELEASED = "resumed released to the browser"
UPSTREAM = "up:"
DOWNSTREAM = "down:"
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
window.rejectNextAnswerApplication = false;
window.answerCreationsStarted = 0;
window.answerCreationsSettled = 0;
window.answerCreationGate = null;
window.answerApplicationGate = null;
const nativeCreateAnswer = peerConnectionPrototype.createAnswer;
peerConnectionPrototype.createAnswer = function (options) {
    window.answerCreationsStarted += 1;
    const created = nativeCreateAnswer.call(this, options);
    const heldUntilReleased = window.answerCreationGate ? window.answerCreationGate.then(() => created) : created;
    return heldUntilReleased.finally(() => { window.answerCreationsSettled += 1; });
};
window.dispatchStateChanges = (connection) => {
    connection.dispatchEvent(new Event("iceconnectionstatechange"));
    connection.dispatchEvent(new Event("connectionstatechange"));
};
window.localUfrags = async (connection) => {
    const stats = await connection.getStats();
    const transport = [...stats.values()].find((report) => report.type === "transport");
    const description = connection.localDescription.sdp;
    return { statsUfrag: transport.iceLocalUsernameFragment, sdpUfrag: /a=ice-ufrag:(\\S+)/.exec(description)[1] };
};
"""
FORCE_STATE = """
(state) => {
    const connection = window.recordedConnections[0];
    window.forcedStates.set(connection, state);
    window.dispatchStateChanges(connection);
}
"""
CLEAR_FORCED_STATE = """
() => {
    const connection = window.recordedConnections[0];
    window.forcedStates.delete(connection);
    window.dispatchStateChanges(connection);
}
"""
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
AFTER_PENDING_REJECTIONS_ARE_REPORTED = """
() => new Promise((done) => {
    const channel = new MessageChannel();
    channel.port1.onmessage = () => done();
    channel.port2.postMessage(0);
})
"""
ANSWER_CREATION_SETTLED = "(before) => window.answerCreationsSettled > before"
RECONNECT_TO_SAME_SEAT = 'async () => (await import("/static/js/signaling.js")).connect()'
LOCAL_DESCRIPTIONS_SET = "(count) => window.localDescriptionCount >= count"
STATUS_IS = '(text) => document.getElementById("status").textContent === text'
IN_CALL = "In call"
CONNECTION_COUNT_AND_OPEN = """
() => window.recordedConnections.length === 1 && window.recordedConnections[0].signalingState !== "closed"
"""
VIDEO_TRANSCEIVERS_PER_CONNECTION = """
() => window.recordedConnections
    .filter((connection) => connection.signalingState !== "closed")
    .map((connection) => connection.getTransceivers()
        .filter((transceiver) => transceiver.receiver.track.kind === "video").length)
"""
A_NEW_CONNECTION_IS_CONNECTED = """
() => window.recordedConnections.slice(1).some(
    (connection) => ["connected", "completed"].includes(connection.iceConnectionState)
)
"""
FIRST_CONNECTION_IS_CLOSED = '() => window.recordedConnections[0].signalingState === "closed"'
RECONNECT_DELAYS = """
async () => (await import("/static/js/signaling.js")).RECONNECT_DELAYS_MS
"""
SEND_CHAT_FRAME = """
async () => (await import("/static/js/signaling.js")).send({ type: "chat", message: "no session" })
"""
RECONNECT_ATTEMPT = 'async () => (await import("/static/js/state.js")).state.reconnectAttempt'
IS_CONNECTED = 'async () => (await import("/static/js/signaling.js")).isConnected()'
SENT_LOG_REPORT = """
async ({ frames, limit }) => {
    const { SentLog } = await import("/static/js/replay.js");
    const log = new SentLog(limit);
    const numbered = frames.map((frame) => log.append(frame));
    return { seqs: numbered.map((frame) => frame.seq), floor: log.floor, kept: log.unacknowledged() };
}
"""
REPLAY_LOG_LIMIT_FROM_MODULE = 'async () => (await import("/static/js/replay.js")).REPLAY_LOG_LIMIT'

SDP_UFRAG = "async () => (await window.localUfrags(window.recordedConnections[0])).sdpUfrag"
UFRAG_ROTATED_AND_IN_STATS = """
async (previousUfrag) => {
    const { statsUfrag, sdpUfrag } = await window.localUfrags(window.recordedConnections[0]);
    return sdpUfrag !== previousUfrag && statsUfrag === sdpUfrag;
}
"""
RESTART_DELAY = """
async (attempt) => (await import("/static/js/recovery.js")).restartDelay(attempt)
"""
DISCONNECTED_GRACE = """
async () => (await import("/static/js/recovery.js")).ICE_DISCONNECTED_GRACE_MS
"""
VIDEO_CAP_FROM_MODULE = """
async (remoteCount) => (await import("/static/js/bandwidth.js")).videoBitrateCap(remoteCount)
"""
QUALITY_OF_ROUND_TRIP_FROM_MODULE = """
async (seconds) => (await import("/static/js/quality.js")).qualityOfRoundTrip(seconds)
"""
BLANK_QUALITY_BARS = """
() => document.querySelectorAll(".connection-quality").forEach((indicator) => {
    delete indicator.dataset.quality;
})
"""
QUALITY_BARS_PAINTED = """
() => document.querySelector(".connection-quality[data-quality]") !== null
"""
EVERY_VIDEO_TARGET_IS_AT_MOST = """
async ([peerCount, ceiling]) => {
    const open = window.recordedConnections.filter((connection) => connection.signalingState !== "closed");
    const reports = (await Promise.all(open.map((connection) => connection.getStats())))
        .flatMap((stats) => [...stats.values()])
        .filter((report) => report.type === "outbound-rtp" && report.kind === "video");
    return reports.length === peerCount && reports.every((report) => report.targetBitrate <= ceiling);
}
"""


@dataclass
class OfferDrops:
    remaining: int = 0

    def should_drop(self, frame: str | bytes) -> bool:
        if self.remaining <= 0 or json.loads(frame)["type"] != OFFER_FRAME_TYPE:
            return False
        self.remaining -= 1
        return True


@dataclass
class SignalingTap:
    """Sits on one person's signaling socket, records what it carries and can damage the connection."""

    offer_drops: OfferDrops = field(default_factory=OfferDrops)
    urls: list[str] = field(default_factory=list)
    upstream: list[dict[str, Any]] = field(default_factory=list)
    downstream: list[dict[str, Any]] = field(default_factory=list)
    timeline: list[str] = field(default_factory=list)
    swallow_upstream: int = 0
    blackhole_downstream: bool = False
    refused_connections: int = 0
    corrupt_resume_token: bool = False
    deliver_chats_twice: bool = False
    hold_resumed: bool = False
    held_resumed: list[str | bytes] = field(default_factory=list)
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
        self.timeline.append(RESUMED_RELEASED)
        for frame in self.held_resumed:
            self._sides[0].send(frame)
        self.held_resumed.clear()

    def _to_server(self, server_side: WebSocketRoute, frame: str | bytes, *, refusing: bool) -> None:
        message = json.loads(frame)
        self.upstream.append(message)
        self.timeline.append(f"{UPSTREAM}{message['type']}")
        if refusing:
            self.cut()
        elif self.swallow_upstream > 0:
            self.swallow_upstream -= 1
        elif not self.offer_drops.should_drop(frame):
            if self.corrupt_resume_token and message["type"] == RESUME_FRAME_TYPE:
                frame = json.dumps({**message, "token": message["token"] + "x"})
            server_side.send(frame)

    def _to_browser(self, browser_side: WebSocketRoute, frame: str | bytes) -> None:
        message = json.loads(frame)
        self.downstream.append(message)
        self.timeline.append(f"{DOWNSTREAM}{message['type']}")
        if self.blackhole_downstream:
            return
        if self.hold_resumed and message["type"] == RESUMED_FRAME_TYPE:
            self.held_resumed.append(frame)
        else:
            browser_side.send(frame)
            if self.deliver_chats_twice and message["type"] == CHAT_FRAME_TYPE:
                browser_side.send(frame)

    def types(self, direction: list[dict[str, Any]]) -> list[str]:
        return [message["type"] for message in direction]


@dataclass
class ConnectedPair:
    host: Page
    guest: Page
    host_tap: SignalingTap
    guest_tap: SignalingTap
    invite_link: str
    page_errors: list[str]

    @property
    def guest_offer_drops(self) -> OfferDrops:
        return self.guest_tap.offer_drops

    def force_failed_on(self, person: Page) -> None:
        person.evaluate(FORCE_STATE, "failed")

    def tap_of(self, person: Page) -> SignalingTap:
        return self.host_tap if person is self.host else self.guest_tap

    def other_than(self, person: Page) -> Page:
        return self.guest if person is self.host else self.host

    def cut(self, person: Page) -> None:
        self.tap_of(person).cut()
        wait_for_status(person, _reconnecting_status(_reconnect_delays(person)[0]))

    def resume(self, person: Page) -> None:
        person.clock.run_for(_reconnect_delays(person)[0])
        wait_for_status(person, IN_CALL)


def _reconnect_delays(person: Page) -> list[int]:
    return [int(delay) for delay in person.evaluate(RECONNECT_DELAYS)]


def _reconnecting_status(delay_ms: int) -> str:
    return f"Reconnecting in {delay_ms // 1000}s..."


def wait_for_status(person: Page, text: str) -> None:
    person.wait_for_function(STATUS_IS, arg=text, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


def pump_until(
    person: Page,
    condition: Callable[[], bool],
    deadline_seconds: int = PUMP_DEADLINE_SECONDS,
    failure: str = "the awaited signaling traffic never happened",
) -> None:
    deadline = time.monotonic() + deadline_seconds
    while not condition():
        assert time.monotonic() < deadline, failure
        person.evaluate("() => 0")


def _restart_delay(person: Page, attempt: int) -> int:
    return int(person.evaluate(RESTART_DELAY, attempt))


def _restart_offers(person: Page) -> int:
    return int(person.evaluate(RESTART_OFFER_COUNT))


def _wait_for_new_ufrag_in_stats(person: Page, previous_ufrag: str) -> str:
    pump_until(person, lambda: bool(person.evaluate(UFRAG_ROTATED_AND_IN_STATS, previous_ufrag)))
    return str(person.evaluate(SDP_UFRAG))


def _connect_pair(
    open_person: Callable[..., Page], base_url: str, guest_init_scripts: tuple[str, ...] = ()
) -> ConnectedPair:
    host = open_person()
    guest = open_person()
    host_tap, guest_tap = SignalingTap(), SignalingTap()
    page_errors: list[str] = []
    for person, tap in ((host, host_tap), (guest, guest_tap)):
        person.route_web_socket("**/ws?*", tap.relay)
        person.on("pageerror", lambda error: page_errors.append(str(error)))
        person.clock.install()
        person.add_init_script(RECORD_PEER_CONNECTIONS)
        person.add_init_script(FORCE_ICE_FAULTS)
        person.add_init_script(COUNT_OFFERS)

    guest.goto("about:blank")
    for script in guest_init_scripts:
        guest.add_init_script(script)
    invite_link = start_call(host, base_url)
    guest.goto(invite_link)
    for person in (host, guest):
        person.wait_for_function(FIRST_CONNECTION_IS_CONNECTED, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
        person.clock.pause_at(datetime.now(tz=UTC) + CLOCK_PAUSE_LEAD)
    return ConnectedPair(host, guest, host_tap, guest_tap, invite_link, page_errors)


@pytest.fixture
def connected_pair(open_person: Callable[..., Page], base_url: str) -> Iterator[ConnectedPair]:
    pair = _connect_pair(open_person, base_url)
    yield pair
    assert pair.page_errors == []


@pytest.fixture
def camera_less_guest_pair(open_person: Callable[..., Page], base_url: str) -> Iterator[ConnectedPair]:
    pair = _connect_pair(open_person, base_url, guest_init_scripts=(REFUSE_THE_CAMERA,))
    yield pair
    assert pair.page_errors == []


def test_forced_failure_on_the_joiner_restarts_ice_once_and_both_sides_take_the_new_ufrag(
    connected_pair: ConnectedPair,
) -> None:
    guest, host = connected_pair.guest, connected_pair.host
    guest_before, host_before = str(guest.evaluate(SDP_UFRAG)), str(host.evaluate(SDP_UFRAG))

    connected_pair.force_failed_on(guest)

    assert _restart_offers(guest) == 1
    assert _wait_for_new_ufrag_in_stats(guest, guest_before) != guest_before
    assert _wait_for_new_ufrag_in_stats(host, host_before) != host_before
    guest.clock.run_for(_restart_delay(guest, 0))
    assert _restart_offers(guest) == 1


def test_disconnected_joiner_restarts_after_the_grace_and_not_before(connected_pair: ConnectedPair) -> None:
    guest = connected_pair.guest
    grace = int(guest.evaluate(DISCONNECTED_GRACE))

    guest.evaluate(FORCE_STATE, "disconnected")
    guest.clock.run_for(grace - 1)
    assert _restart_offers(guest) == 0
    guest.clock.run_for(1)

    assert _restart_offers(guest) == 1


def test_disconnected_joiner_that_recovers_within_the_grace_does_not_restart(connected_pair: ConnectedPair) -> None:
    guest = connected_pair.guest
    grace = int(guest.evaluate(DISCONNECTED_GRACE))

    guest.evaluate(FORCE_STATE, "disconnected")
    guest.clock.run_for(grace // 2)
    guest.evaluate(CLEAR_FORCED_STATE)
    guest.clock.run_for(grace)

    assert _restart_offers(guest) == 0


def test_lost_restart_offer_is_retried_from_have_local_offer_and_the_retry_schedule_resets(
    connected_pair: ConnectedPair,
) -> None:
    guest, host = connected_pair.guest, connected_pair.host
    first_delay = _restart_delay(guest, 0)
    guest_before, host_before = str(guest.evaluate(SDP_UFRAG)), str(host.evaluate(SDP_UFRAG))
    connected_pair.guest_offer_drops.remaining = 1

    connected_pair.force_failed_on(guest)
    guest.wait_for_function(AWAITING_ANSWER, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    guest.clock.run_for(first_delay)

    assert _restart_offers(guest) == 2
    assert _wait_for_new_ufrag_in_stats(guest, guest_before) != guest_before
    assert _wait_for_new_ufrag_in_stats(host, host_before) != host_before

    guest.clock.run_for(_restart_delay(guest, 1))
    assert _restart_offers(guest) == 2
    connected_pair.guest_offer_drops.remaining = 1
    connected_pair.force_failed_on(guest)
    assert _restart_offers(guest) == 3
    guest.clock.run_for(first_delay)
    assert _restart_offers(guest) == 4


def test_forced_failure_on_the_answerer_never_sends_a_restart_offer(connected_pair: ConnectedPair) -> None:
    host = connected_pair.host

    connected_pair.force_failed_on(host)
    host.clock.run_for(_restart_delay(host, 0) + _restart_delay(host, 1))

    assert _restart_offers(host) == 0


def test_quality_indicator_keeps_polling_through_a_failure_and_after_the_recovery(
    connected_pair: ConnectedPair,
) -> None:
    guest = connected_pair.guest
    connected_pair.guest_offer_drops.remaining = UNLIMITED_DROPS
    guest.clock.run_for(QUALITY_POLL_INTERVAL_MS)
    guest.wait_for_function(QUALITY_BARS_PAINTED, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

    connected_pair.force_failed_on(guest)
    guest.clock.run_for(QUALITY_POLL_INTERVAL_MS)
    connected_pair.guest_offer_drops.remaining = 0
    guest.clock.run_for(_restart_delay(guest, 1))
    guest.wait_for_function(FIRST_CONNECTION_IS_CONNECTED, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    guest.evaluate(BLANK_QUALITY_BARS)
    guest.clock.run_for(QUALITY_POLL_INTERVAL_MS)

    guest.wait_for_function(QUALITY_BARS_PAINTED, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


@pytest.mark.parametrize(
    ("attempt", "expected_delay"),
    [(0, 2000), (1, 4000), (2, 8000), (3, 15000), (4, 15000), (5, 15000)],
)
def test_restart_delay_follows_the_schedule_and_repeats_the_last_delay(
    open_person: Callable[..., Page], base_url: str, attempt: int, expected_delay: int
) -> None:
    page = open_person()
    page.goto(f"{base_url}/")

    assert _restart_delay(page, attempt) == expected_delay


@pytest.mark.parametrize(
    ("remote_count", "expected_cap"),
    [(1, 2_000_000), (2, 1_000_000), (3, 666_666), (4, 500_000), (5, 400_000)],
)
def test_video_bitrate_cap_divides_the_budget_among_the_remote_peers(
    open_person: Callable[..., Page], base_url: str, remote_count: int, expected_cap: int
) -> None:
    page = open_person()
    page.goto(f"{base_url}/")

    assert page.evaluate(VIDEO_CAP_FROM_MODULE, remote_count) == expected_cap


def _wait_for_video_caps(people: list[Page], peer_count: int, expected_cap: int) -> None:
    for person in people:
        wait_for_peer_count(person, peer_count)
        person.wait_for_function(
            EVERY_VIDEO_CAP_IS, arg=[peer_count, expected_cap], timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS
        )


def test_six_person_call_caps_every_video_upload_by_call_size(open_person: Callable[..., Page], base_url: str) -> None:
    people = [open_person() for _ in range(SIX_PEOPLE)]
    for person in people:
        person.add_init_script(RECORD_PEER_CONNECTIONS)
    invite_link = start_call(people[0], base_url)
    for joined, person in enumerate(people[1:], start=2):
        person.goto(invite_link)
        for earlier in people[:joined]:
            wait_for_peer_count(earlier, joined - 1)
        if joined == 3:
            _wait_for_video_caps(people[:joined], 2, 1_000_000)

    _wait_for_video_caps(people, 5, 400_000)
    for person in people:
        person.wait_for_function(
            EVERY_VIDEO_TARGET_IS_AT_MOST, arg=[5, 400_000], timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS
        )

    people[-1].close()
    _wait_for_video_caps(people[:-1], 4, 500_000)


def _wait_for_reconnect_attempt(person: Page, expected: int) -> None:
    pump_until(person, lambda: person.evaluate(RECONNECT_ATTEMPT) == expected)


def _inbound_video_bytes(person: Page) -> int:
    return int(sum(inbound_per_connection(person, "video", "bytesReceived")))


def _wait_for_more_video_bytes_than(person: Page, baseline: int, deadline_seconds: int = PUMP_DEADLINE_SECONDS) -> int:
    pump_until(
        person,
        lambda: _inbound_video_bytes(person) > baseline,
        deadline_seconds,
        f"no new video bytes arrived within {deadline_seconds}s",
    )
    return _inbound_video_bytes(person)


def _frames_of(tap: SignalingTap, direction: str, frame_type: str, since: int = 0) -> list[dict[str, Any]]:
    return [message for message in getattr(tap, direction)[since:] if message["type"] == frame_type]


def _chat_messages_heard_by(person: Page) -> list[str]:
    return person.locator("#chatMessages .chat-msg.other").all_inner_texts()


def _say_in_chat(person: Page, text: str) -> None:
    if not person.locator("#chatPanel.visible").count():
        person.click("#chatBtn")
    person.fill("#chatInput", text)
    person.click("#chatSend")


@pytest.mark.parametrize("cut_guest", [True, False], ids=["guest_cut", "host_cut"])
def test_cut_signaling_socket_resumes_the_seat_while_the_video_keeps_flowing_and_nobody_hears_a_leave_or_join(
    connected_pair: ConnectedPair, cut_guest: bool
) -> None:
    cut_person = connected_pair.guest if cut_guest else connected_pair.host
    observer = connected_pair.other_than(cut_person)
    cut_tap, observer_tap = connected_pair.tap_of(cut_person), connected_pair.tap_of(observer)
    heard_before = len(observer_tap.downstream)
    bytes_before = _inbound_video_bytes(cut_person)

    connected_pair.cut(cut_person)
    bytes_during_the_cut = _wait_for_more_video_bytes_than(cut_person, bytes_before, CUT_VIDEO_DEADLINE_SECONDS)
    connected_pair.resume(cut_person)
    _wait_for_more_video_bytes_than(cut_person, bytes_during_the_cut)

    for person in (connected_pair.host, connected_pair.guest):
        assert person.evaluate(CONNECTION_COUNT_AND_OPEN)
    heard_after = observer_tap.types(observer_tap.downstream[heard_before:])
    assert {"participant_left", "participant_joined", "host_away", "host_back"}.isdisjoint(heard_after)
    sent = cut_tap.types(cut_tap.upstream)
    assert (sent.count("join"), sent.count(RESUME_FRAME_TYPE)) == (1, 1)


def test_restart_answer_swallowed_at_the_cut_is_resent_after_the_resume_and_both_sides_take_the_new_ufrag(
    connected_pair: ConnectedPair,
) -> None:
    guest, host = connected_pair.guest, connected_pair.host
    guest_before, host_before = str(guest.evaluate(SDP_UFRAG)), str(host.evaluate(SDP_UFRAG))
    answers_before = len(_frames_of(connected_pair.guest_tap, "downstream", "answer"))
    upstream_before = len(connected_pair.host_tap.upstream)
    host_tap = connected_pair.host_tap
    host_tap.swallow_upstream = UNLIMITED_DROPS

    connected_pair.force_failed_on(guest)
    pump_until(host, lambda: bool(_frames_of(host_tap, "upstream", "answer", since=upstream_before)))
    connected_pair.cut(host)
    host_tap.swallow_upstream = 0
    connected_pair.resume(host)

    assert _wait_for_new_ufrag_in_stats(guest, guest_before) != guest_before
    assert _wait_for_new_ufrag_in_stats(host, host_before) != host_before
    _say_in_chat(host, "sent after the answer")
    expect(guest.locator("#chatMessages .chat-msg.other")).to_have_count(1)
    assert len(_frames_of(connected_pair.guest_tap, "downstream", "answer")) == answers_before + 1


def test_chat_swallowed_at_the_cut_reaches_the_other_side_exactly_once_after_the_resume(
    connected_pair: ConnectedPair,
) -> None:
    guest, host = connected_pair.guest, connected_pair.host
    connected_pair.guest_tap.swallow_upstream = 1

    _say_in_chat(guest, "lost in the cut")
    pump_until(guest, lambda: connected_pair.guest_tap.swallow_upstream == 0)
    connected_pair.cut(guest)
    connected_pair.resume(guest)

    expect(host.locator("#chatMessages .chat-msg.other")).to_have_count(1)
    [heard] = _chat_messages_heard_by(host)
    assert heard.endswith("lost in the cut")
    assert len(_frames_of(connected_pair.host_tap, "downstream", "chat")) == 1


def test_chat_typed_while_the_socket_is_down_is_written_in_order_after_the_resume(
    connected_pair: ConnectedPair,
) -> None:
    guest, host = connected_pair.guest, connected_pair.host
    connected_pair.cut(guest)

    _say_in_chat(guest, "first")
    _say_in_chat(guest, "second")
    connected_pair.resume(guest)

    expect(host.locator("#chatMessages .chat-msg.other")).to_have_count(2)
    sent = connected_pair.guest_tap.upstream
    last_resume = max(index for index, message in enumerate(sent) if message["type"] == RESUME_FRAME_TYPE)
    assert [message["message"] for message in sent[last_resume + 1 :] if message["type"] == CHAT_FRAME_TYPE] == [
        "first",
        "second",
    ]


def test_two_restart_offers_made_while_the_socket_is_down_reach_the_host_as_one(
    connected_pair: ConnectedPair,
) -> None:
    guest, host = connected_pair.guest, connected_pair.host
    first_delay = _reconnect_delays(guest)[0]
    second_delay = _reconnect_delays(guest)[1]
    offers_before = len(_frames_of(connected_pair.host_tap, "downstream", "offer"))
    descriptions_before = int(guest.evaluate("() => window.localDescriptionCount"))
    connected_pair.guest_tap.refused_connections = 1
    connected_pair.cut(guest)

    connected_pair.force_failed_on(guest)
    guest.wait_for_function(
        LOCAL_DESCRIPTIONS_SET, arg=descriptions_before + 1, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS
    )
    guest.clock.run_for(first_delay)
    wait_for_status(guest, _reconnecting_status(second_delay))
    guest.clock.run_for(_restart_delay(guest, 0) - first_delay)
    guest.wait_for_function(
        LOCAL_DESCRIPTIONS_SET, arg=descriptions_before + 2, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS
    )
    guest.clock.run_for(second_delay - (_restart_delay(guest, 0) - first_delay))
    wait_for_status(guest, IN_CALL)
    _say_in_chat(guest, "sent after both offers")
    expect(host.locator("#chatMessages .chat-msg.other")).to_have_count(1)

    assert _restart_offers(guest) == 2
    assert len(_frames_of(connected_pair.host_tap, "downstream", "offer")) == offers_before + 1
    assert host.evaluate(CONNECTION_COUNT_AND_OPEN)


def test_answer_arriving_while_an_earlier_answer_is_being_applied_waits_for_it_and_raises_no_page_error(
    connected_pair: ConnectedPair,
) -> None:
    guest, host = connected_pair.guest, connected_pair.host
    first_delay, second_delay = _reconnect_delays(guest)[:2]
    guest_before, host_before = str(guest.evaluate(SDP_UFRAG)), str(host.evaluate(SDP_UFRAG))
    answers_before = len(_frames_of(connected_pair.guest_tap, "downstream", ANSWER_FRAME_TYPE))
    answers_sent = len(_frames_of(connected_pair.host_tap, "upstream", ANSWER_FRAME_TYPE))
    guest_tap, host_tap = connected_pair.guest_tap, connected_pair.host_tap
    guest_tap.blackhole_downstream = True
    guest_tap.refused_connections = 1
    guest.evaluate(HOLD_ANSWER_APPLICATION)

    connected_pair.force_failed_on(guest)
    pump_until(guest, lambda: len(_frames_of(guest_tap, "downstream", ANSWER_FRAME_TYPE)) > answers_before)
    connected_pair.cut(guest)
    guest_tap.blackhole_downstream = False
    guest.clock.run_for(first_delay)
    wait_for_status(guest, _reconnecting_status(second_delay))
    guest.clock.run_for(_restart_delay(guest, 0) - first_delay)
    pump_until(guest, lambda: _restart_offers(guest) == 2)
    guest.clock.run_for(second_delay - (_restart_delay(guest, 0) - first_delay))
    wait_for_status(guest, IN_CALL)
    pump_until(host, lambda: len(_frames_of(host_tap, "upstream", ANSWER_FRAME_TYPE)) == answers_sent + 2)
    _say_in_chat(host, "sent after both answers")
    expect(guest.locator("#chatMessages .chat-msg.other")).to_have_count(1)
    guest.evaluate(RELEASE_ANSWER_APPLICATION)
    guest.evaluate(AFTER_PENDING_REJECTIONS_ARE_REPORTED)

    assert _wait_for_new_ufrag_in_stats(guest, guest_before) != guest_before
    assert _wait_for_new_ufrag_in_stats(host, host_before) != host_before


def test_rejected_answer_application_is_reported_and_the_next_answer_is_still_applied(
    connected_pair: ConnectedPair,
) -> None:
    guest, host = connected_pair.guest, connected_pair.host
    first_delay = _restart_delay(guest, 0)
    guest_before, host_before = str(guest.evaluate(SDP_UFRAG)), str(host.evaluate(SDP_UFRAG))
    guest.evaluate(REJECT_NEXT_ANSWER_APPLICATION, INJECTED_REJECTION)

    connected_pair.force_failed_on(guest)
    pump_until(guest, lambda: bool(connected_pair.page_errors))
    guest.clock.run_for(first_delay)

    guest.wait_for_function(ANSWER_APPLIED, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    assert [INJECTED_REJECTION in error for error in connected_pair.page_errors] == [True]
    connected_pair.page_errors.pop()
    assert _wait_for_new_ufrag_in_stats(guest, guest_before) != guest_before
    assert _wait_for_new_ufrag_in_stats(host, host_before) != host_before


def test_every_cut_in_a_row_resumes_with_the_first_delay_because_each_resume_resets_the_attempts(
    connected_pair: ConnectedPair,
) -> None:
    guest = connected_pair.guest

    for _ in range(CUTS_IN_A_ROW):
        connected_pair.cut(guest)
        connected_pair.resume(guest)

        assert guest.evaluate(RECONNECT_ATTEMPT) == 0


def test_server_frame_delivered_twice_is_dispatched_once(connected_pair: ConnectedPair) -> None:
    guest, host = connected_pair.guest, connected_pair.host
    connected_pair.guest_tap.deliver_chats_twice = True

    _say_in_chat(host, "once")
    _say_in_chat(host, "marker")

    expect(guest.locator("#chatMessages .chat-msg.other")).to_have_text([re.compile("once$"), re.compile("marker$")])


def test_events_of_a_replaced_socket_are_ignored(connected_pair: ConnectedPair) -> None:
    guest = connected_pair.guest
    first_delay = _reconnect_delays(guest)[0]

    guest.evaluate(RECONNECT_TO_SAME_SEAT)
    pump_until(guest, lambda: bool(_frames_of(connected_pair.guest_tap, "downstream", RESUMED_FRAME_TYPE)))
    wait_for_status(guest, IN_CALL)
    guest.clock.run_for(first_delay)
    _say_in_chat(guest, "still connected")

    expect(connected_pair.host.locator("#chatMessages .chat-msg.other")).to_have_count(1)
    assert len(connected_pair.guest_tap.urls) == 2
    assert guest.evaluate(RECONNECT_ATTEMPT) == 0


def test_chat_typed_while_the_socket_is_open_but_not_yet_resumed_waits_for_the_resumed_frame(
    connected_pair: ConnectedPair,
) -> None:
    guest, host = connected_pair.guest, connected_pair.host
    tap = connected_pair.guest_tap
    tap.hold_resumed = True
    connected_pair.cut(guest)
    guest.clock.run_for(_reconnect_delays(guest)[0])
    pump_until(guest, lambda: bool(tap.held_resumed))

    _say_in_chat(guest, "typed while resuming")
    tap.release_held_resumed()

    expect(host.locator("#chatMessages .chat-msg.other")).to_have_count(1)
    assert tap.timeline.index(f"{UPSTREAM}{CHAT_FRAME_TYPE}") > tap.timeline.index(RESUMED_RELEASED)
    assert len(_frames_of(connected_pair.host_tap, "downstream", CHAT_FRAME_TYPE)) == 1


def test_refused_resume_falls_back_to_a_fresh_join_with_new_peer_connections(
    connected_pair: ConnectedPair,
) -> None:
    guest = connected_pair.guest
    connected_pair.guest_tap.corrupt_resume_token = True

    connected_pair.cut(guest)
    guest.clock.run_for(_reconnect_delays(guest)[0])
    guest.wait_for_function(A_NEW_CONNECTION_IS_CONNECTED, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

    assert guest.evaluate(FIRST_CONNECTION_IS_CLOSED)
    sent = connected_pair.guest_tap.types(connected_pair.guest_tap.upstream)
    assert (sent.count("join"), sent.count(RESUME_FRAME_TYPE)) == (2, 1)
    refusals = _frames_of(connected_pair.guest_tap, "downstream", "error")
    assert [refusal["reason"] for refusal in refusals] == ["resume_refused"]


def test_reconnects_that_are_all_refused_end_in_connection_lost_after_the_whole_window(
    connected_pair: ConnectedPair,
) -> None:
    guest = connected_pair.guest
    delays = _reconnect_delays(guest)
    connected_pair.guest_tap.refused_connections = len(delays)
    connected_pair.cut(guest)

    for attempt, delay in enumerate(delays, start=1):
        guest.clock.run_for(delay)
        if attempt < len(delays):
            _wait_for_reconnect_attempt(guest, attempt + 1)
    wait_for_status(guest, "Connection lost")

    assert not guest.evaluate(IS_CONNECTED)


def test_reconnect_delays_cover_the_resume_window_and_stop_before_it_ends(connected_pair: ConnectedPair) -> None:
    delays = _reconnect_delays(connected_pair.guest)
    window_ms = RESUME_WINDOW_SECONDS * 1000

    assert window_ms - delays[-1] <= sum(delays) < window_ms


def test_sending_without_a_session_throws(open_person: Callable[..., Page], base_url: str) -> None:
    page = open_person()
    page.goto(f"{base_url}/")

    with pytest.raises(PlaywrightError, match="No signaling session"):
        page.evaluate(SEND_CHAT_FRAME)


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
    newcomer.goto(connected_pair.invite_link)
    host.wait_for_function(ANSWER_CREATION_STARTED, arg=started_before, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

    host.click("#leaveBtn")
    host.click("#leaveYes")
    host.locator("[data-screen=end]").wait_for(timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    host.evaluate(RELEASE_ANSWER_CREATION)

    host.wait_for_function(ANSWER_CREATION_SETTLED, arg=settled_before, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    host.evaluate(AFTER_PENDING_REJECTIONS_ARE_REPORTED)


def test_peer_removed_in_the_middle_of_a_restart_episode_stops_the_restart_schedule(
    connected_pair: ConnectedPair,
) -> None:
    guest, host = connected_pair.guest, connected_pair.host
    connected_pair.guest_offer_drops.remaining = UNLIMITED_DROPS
    connected_pair.force_failed_on(guest)
    assert _restart_offers(guest) == 1

    host.click("#leaveBtn")
    host.click("#leaveYes")
    wait_for_peer_count(guest, 0)
    guest.clock.run_for(sum(_restart_delay(guest, attempt) for attempt in range(6)))

    assert _restart_offers(guest) == 1


def test_last_peer_leaving_raises_no_page_error_on_the_one_who_stays(connected_pair: ConnectedPair) -> None:
    guest, host = connected_pair.guest, connected_pair.host

    guest.click("#leaveBtn")

    wait_for_peer_count(host, 0)
    host.clock.run_for(sum(_restart_delay(host, attempt) for attempt in range(6)))


@pytest.mark.parametrize(
    ("message_types", "expected_kept"),
    [
        pytest.param(["chat", "chat"], [1, 2], id="plain_frames_all_kept"),
        pytest.param(
            ["offer", "ice_candidate", "offer"], [3], id="newer_offer_drops_the_earlier_description_and_candidate"
        ),
        pytest.param(
            ["answer", "ice_candidate", "answer", "ice_candidate"], [3, 4], id="candidates_after_a_description_stay"
        ),
    ],
)
def test_sent_log_numbers_frames_and_drops_what_a_newer_description_supersedes(
    open_person: Callable[..., Page], base_url: str, message_types: list[str], expected_kept: list[int]
) -> None:
    page = open_person()
    page.goto(f"{base_url}/")
    frames = [{"type": message_type, "target": "peer"} for message_type in message_types]

    report = page.evaluate(SENT_LOG_REPORT, {"frames": frames, "limit": REPLAY_LOG_LIMIT})

    assert report["seqs"] == list(range(1, len(frames) + 1))
    assert [frame["seq"] for frame in report["kept"]] == expected_kept
    assert report["floor"] == 0


def test_sent_log_keeps_at_most_the_server_log_limit_and_raises_the_floor(
    open_person: Callable[..., Page], base_url: str
) -> None:
    page = open_person()
    page.goto(f"{base_url}/")
    frames = [{"type": "chat", "message": str(index)} for index in range(REPLAY_LOG_LIMIT + 3)]

    report = page.evaluate(SENT_LOG_REPORT, {"frames": frames, "limit": page.evaluate(REPLAY_LOG_LIMIT_FROM_MODULE)})

    assert report["floor"] == 3
    assert len(report["kept"]) == REPLAY_LOG_LIMIT


@pytest.mark.parametrize(
    ("round_trip_seconds", "expected_quality"),
    [(0.05, "good"), (0.299, "good"), (0.3, "fair"), (0.499, "fair"), (0.5, "poor"), (2, "poor")],
)
def test_quality_of_round_trip_is_good_below_300_ms_fair_below_500_ms_and_poor_above(
    open_person: Callable[..., Page], base_url: str, round_trip_seconds: float, expected_quality: str
) -> None:
    page = open_person()
    page.goto(f"{base_url}/")

    assert page.evaluate(QUALITY_OF_ROUND_TRIP_FROM_MODULE, round_trip_seconds) == expected_quality


def _frames_decoded(person: Page) -> list[float]:
    return inbound_per_connection(person, "video", "framesDecoded")


def test_camera_turned_on_mid_call_reaches_the_host_without_an_offer_and_survives_an_ice_restart(
    camera_less_guest_pair: ConnectedPair,
) -> None:
    guest, host = camera_less_guest_pair.guest, camera_less_guest_pair.host
    assert _frames_decoded(host) == [0]

    turn_the_camera_on(guest)
    wait_for_frames_to_advance(host)

    assert (_restart_offers(guest), guest.evaluate(OFFER_COUNT)) == (0, 1)
    guest_before = str(guest.evaluate(SDP_UFRAG))
    camera_less_guest_pair.force_failed_on(guest)
    assert _restart_offers(guest) == 1
    assert _wait_for_new_ufrag_in_stats(guest, guest_before) != guest_before
    wait_for_frames_to_advance(host)
    for person in (host, guest):
        assert person.evaluate(VIDEO_TRANSCEIVERS_PER_CONNECTION) == [1]


def test_camera_turned_on_while_the_socket_is_down_reaches_the_host_and_its_media_state_follows_the_resume(
    camera_less_guest_pair: ConnectedPair,
) -> None:
    guest, host = camera_less_guest_pair.guest, camera_less_guest_pair.host
    host_tap = camera_less_guest_pair.host_tap
    heard_before = len(host_tap.downstream)
    camera_less_guest_pair.cut(guest)

    turn_the_camera_on(guest)
    wait_for_frames_to_advance(host)

    assert guest.evaluate(OFFER_COUNT) == 1
    expect(host.locator(OTHER_CAMERA_OFF)).to_be_visible()
    camera_less_guest_pair.resume(guest)
    expect(host.locator(OTHER_CAMERA_OFF)).to_be_hidden()
    heard_after = host_tap.types(host_tap.downstream[heard_before:])
    assert {"participant_left", "participant_joined"}.isdisjoint(heard_after)
