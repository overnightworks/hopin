import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any

from hopin.config.constants import SignalType
from hopin.words import text
from tests.e2e.support.calls import join_by_link, start_call
from tests.e2e.support.media import (
    COUNT_OFFERS,
    MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    RECORD_PEER_CONNECTIONS,
    REFUSE_THE_CAMERA,
    inbound_per_connection,
    poll_until,
)
from tests.e2e.support.signaling import (
    RECONNECT_DELAYS_MS,
    RECORD_SOCKETS,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from playwright.sync_api import Page, WebSocketRoute


CLOCK_PAUSE_LEAD = timedelta(seconds=5)
LEAVE_AND_JOIN_NEWS = {SignalType.PARTICIPANT_LEFT, SignalType.PARTICIPANT_JOINED}
PUMP_DEADLINE_SECONDS = 30
UNLIMITED_DROPS = 1_000_000
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
FIRST_CONNECTION_IS_CONNECTED = """
() => ["connected", "completed"].includes(window.recordedConnections[0]?.iceConnectionState)
"""
RESTART_OFFER_COUNT = "() => window.restartOfferCount"
STATUS_IS = '(text) => document.getElementById("status").textContent === text'
IN_CALL = text("de", "call.statusInCall")
RESTART_DELAY_BY_ATTEMPT = {0: 2000, 1: 4000, 2: 8000, 3: 15000, 4: 15000, 5: 15000}
SDP_UFRAG = "async () => (await window.localUfrags(window.recordedConnections[0])).sdpUfrag"
UFRAG_ROTATED_AND_IN_STATS = """
async (previousUfrag) => {
    const { statsUfrag, sdpUfrag } = await window.localUfrags(window.recordedConnections[0]);
    return sdpUfrag !== previousUfrag && statsUfrag === sdpUfrag;
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
    return text("de", "call.statusReconnecting", count=delay_ms // 1000)


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


def pair_connector(open_person: Callable[..., Page], base_url: str) -> Iterator[Callable[..., ConnectedPair]]:
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


def _inbound_video_bytes(person: Page) -> int:
    return int(sum(inbound_per_connection(person, "video", "bytesReceived")))


def _frames_of(tap: SignalingTap, direction: str, frame_type: str, since: int = 0) -> list[dict[str, Any]]:
    return [message for message in getattr(tap, direction)[since:] if message["type"] == frame_type]


def _heard_since(tap: SignalingTap, since: int) -> set[str]:
    return set(tap.types(tap.downstream[since:]))
