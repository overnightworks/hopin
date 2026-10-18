import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from playwright.sync_api import Page, WebSocketRoute

from tests.e2e.conftest import (
    MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    RECORD_PEER_CONNECTIONS,
    start_call,
    wait_for_peer_count,
)

pytestmark = pytest.mark.e2e

CLOCK_PAUSE_LEAD = timedelta(seconds=5)
QUALITY_POLL_INTERVAL_MS = 3000
OFFER_FRAME_TYPE = "offer"
UNLIMITED_DROPS = 1_000_000
SIX_PEOPLE = 6
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
const nativeSetRemoteDescription = peerConnectionPrototype.setRemoteDescription;
peerConnectionPrototype.setRemoteDescription = function (description) {
    if (description.type === "answer") window.forcedStates.delete(this);
    return nativeSetRemoteDescription.call(this, description);
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
AWAITING_ANSWER = '() => window.recordedConnections[0].signalingState === "have-local-offer"'
RESTART_OFFER_COUNT = "() => window.restartOfferCount"
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
BLANK_QUALITY_BARS = """
() => document.querySelectorAll(".connection-quality .quality-bar").forEach((bar) => {
    bar.className = "quality-bar inactive";
    bar.style.background = "";
})
"""
QUALITY_BARS_PAINTED = """
() => [...document.querySelectorAll(".connection-quality .quality-bar")]
    .some((bar) => !bar.classList.contains("inactive"))
"""
EVERY_VIDEO_CAP_IS = """
([peerCount, expectedCap]) => {
    const open = window.recordedConnections.filter((connection) => connection.signalingState !== "closed");
    const caps = open.flatMap((connection) => connection.getTransceivers()
        .filter((transceiver) => transceiver.receiver.track.kind === "video")
        .map((transceiver) => transceiver.sender.getParameters().encodings[0].maxBitrate));
    return open.length === peerCount && caps.length === peerCount && caps.every((cap) => cap === expectedCap);
}
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
    dropped: list[str] = field(default_factory=list)

    def should_drop(self, frame: str | bytes) -> bool:
        if self.remaining <= 0 or json.loads(frame)["type"] != OFFER_FRAME_TYPE:
            return False
        self.remaining -= 1
        return True


@dataclass
class ConnectedPair:
    host: Page
    guest: Page
    guest_offer_drops: OfferDrops

    def force_failed_on(self, person: Page) -> None:
        person.evaluate(FORCE_STATE, "failed")

    def run_clocks(self, milliseconds: int) -> None:
        for person in (self.host, self.guest):
            person.clock.run_for(milliseconds)


def _relay_with_drops(drops: OfferDrops) -> Callable[[WebSocketRoute], None]:
    def relay(browser_side: WebSocketRoute) -> None:
        server_side = browser_side.connect_to_server()

        def from_browser(frame: str | bytes) -> None:
            if not drops.should_drop(frame):
                server_side.send(frame)

        browser_side.on_message(from_browser)
        server_side.on_message(browser_side.send)
        browser_side.on_close(lambda code, reason: server_side.close(code=code, reason=reason))

    return relay


def _restart_delay(person: Page, attempt: int) -> int:
    return int(person.evaluate(RESTART_DELAY, attempt))


def _restart_offers(person: Page) -> int:
    return int(person.evaluate(RESTART_OFFER_COUNT))


def _wait_for_new_ufrag_in_stats(person: Page, previous_ufrag: str) -> str:
    person.wait_for_function(UFRAG_ROTATED_AND_IN_STATS, arg=previous_ufrag, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    return str(person.evaluate(SDP_UFRAG))


@pytest.fixture
def connected_pair(open_person: Callable[..., Page], base_url: str) -> ConnectedPair:
    host = open_person()
    guest = open_person()
    guest_offer_drops = OfferDrops()
    guest.route_web_socket("**/ws?*", _relay_with_drops(guest_offer_drops))
    for person in (host, guest):
        person.clock.install()
        person.add_init_script(RECORD_PEER_CONNECTIONS)
        person.add_init_script(FORCE_ICE_FAULTS)

    guest.goto("about:blank")
    invite_link = start_call(host, base_url)
    guest.goto(invite_link)
    for person in (host, guest):
        person.wait_for_function(FIRST_CONNECTION_IS_CONNECTED, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
        person.clock.pause_at(datetime.now(tz=UTC) + CLOCK_PAUSE_LEAD)
    return ConnectedPair(host, guest, guest_offer_drops)


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
    page.goto(f"{base_url}/call")

    assert _restart_delay(page, attempt) == expected_delay


@pytest.mark.parametrize(
    ("remote_count", "expected_cap"),
    [(1, 2_000_000), (2, 1_000_000), (3, 666_666), (4, 500_000), (5, 400_000)],
)
def test_video_bitrate_cap_divides_the_budget_among_the_remote_peers(
    open_person: Callable[..., Page], base_url: str, remote_count: int, expected_cap: int
) -> None:
    page = open_person()
    page.goto(f"{base_url}/call")

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
