import re
from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import Expect, Page

from hopin.config.constants import SignalType
from tests.e2e.support.calls import join_by_link, start_call
from tests.e2e.support.media import (
    AFTER_PENDING_REJECTIONS_ARE_REPORTED,
    EVERY_VIDEO_CAP_IS,
    FIVE_CAPS_PER_CONNECTION,
    FOUR_CAPS_PER_CONNECTION,
    MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    OFFER_COUNT,
    OPEN_PEER_COUNT_REACHED,
    OTHER_CAMERA_OFF,
    OTHER_TILE,
    RECORD_PEER_CONNECTIONS,
    TWO_CAPS_PER_CONNECTION,
    VIDEO_TRANSCEIVERS_PER_CONNECTION,
    inbound_per_connection,
    poll_until,
    turn_the_camera_on,
    wait_for_frames_to_advance,
    wait_for_peer_count,
    wait_for_the_picture,
)
from tests.e2e.support.mesh_pair import (
    FIRST_CONNECTION_IS_CONNECTED,
    FORCE_STATE,
    LEAVE_AND_JOIN_NEWS,
    RESTART_DELAY_BY_ATTEMPT,
    UNLIMITED_DROPS,
    ConnectedPair,
    Side,
    _expect_to_reconnect_on_a_new_ufrag,
    _frames_of,
    _heard_since,
    _restart_offers,
    _sdp_ufrag,
    _wait_for,
    _wait_for_new_ufrag_in_stats,
    pump_until,
)

if TYPE_CHECKING:
    from collections.abc import Callable

pytest_plugins = ["tests.e2e.support.mesh_pair"]
pytestmark = pytest.mark.e2e

expect = Expect()
expect.set_options(timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

QUALITY_POLL_INTERVAL_MS = 3000
SIX_PEOPLE = 6
CLEAR_FORCED_STATE = "() => window.forceFirstConnectionInto(null)"
AWAITING_ANSWER = '() => window.recordedConnections[0].signalingState === "have-local-offer"'
HOLD_ANSWER_CREATION = """
() => {
    window.answerCreationGate = new Promise((release) => { window.releaseAnswerCreation = release; });
}
"""
RELEASE_ANSWER_CREATION = "() => window.releaseAnswerCreation()"
ANSWER_CREATIONS_COUNTED = "() => [window.answerCreationsStarted, window.answerCreationsSettled]"
ANSWER_CREATION_STARTED = "(before) => window.answerCreationsStarted > before"
ANSWER_CREATION_SETTLED = "(before) => window.answerCreationsSettled > before"
REMOTE_UFRAG = "() => /a=ice-ufrag:(\\S+)/.exec(window.recordedConnections[0].remoteDescription.sdp)[1]"
ICE_DISCONNECTED_GRACE_MS = 2000
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


def test_forced_failure_on_the_joiner_restarts_ice_once_and_both_sides_take_the_new_ufrag(
    connected_pair: ConnectedPair,
) -> None:
    """CALL-08: A failed connection restarts by itself and both sides take the new ICE credentials."""
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
    """CALL-08: A lost restart offer is retried until the connection is back."""
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
    """CALL-09: Every video upload is capped by the size of the call."""
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
