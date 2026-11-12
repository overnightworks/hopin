import re
from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import Expect, Page

from hopin.config.constants import RESUME_WINDOW_SECONDS, ErrorReason, SignalType
from tests.e2e.support.calls import say_in_chat
from tests.e2e.support.media import (
    AFTER_PENDING_REJECTIONS_ARE_REPORTED,
    MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
)
from tests.e2e.support.mesh_pair import (
    IN_CALL,
    LEAVE_AND_JOIN_NEWS,
    PUMP_DEADLINE_SECONDS,
    RESTART_DELAY_BY_ATTEMPT,
    UNLIMITED_DROPS,
    ConnectedPair,
    Side,
    _expect_to_reconnect_on_a_new_ufrag,
    _frames_of,
    _heard_since,
    _inbound_video_bytes,
    _reconnecting_status,
    _restart_offers,
    _wait_for,
    pump_until,
    wait_for_status,
)
from tests.e2e.support.signaling import (
    LATE_EVENTS_OF_THE_FIRST_SOCKET,
    NEW_SOCKET_IS_CLOSED,
    RECONNECT_DELAYS_MS,
    SOCKETS_OPENED,
)
from tests.support.words import text

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.e2e

expect = Expect()
expect.set_options(timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

HEARD_CHAT = "#chatMessages .chat-msg.other"
HOST_PRESENCE_NEWS = {SignalType.HOST_AWAY, SignalType.HOST_BACK}
CUT_VIDEO_DEADLINE_SECONDS = RESUME_WINDOW_SECONDS // 2
CUTS_IN_A_ROW = 3
ANSWER_APPLIED = '() => window.recordedConnections[0].signalingState === "stable"'
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
LOCAL_DESCRIPTIONS_SET = "(count) => window.localDescriptionCount >= count"
CONNECTION_COUNT_AND_OPEN = """
() => window.recordedConnections.length === 1 && window.recordedConnections[0].signalingState !== "closed"
"""
A_NEW_CONNECTION_IS_CONNECTED = """
() => window.recordedConnections.slice(1).some(
    (connection) => ["connected", "completed"].includes(connection.iceConnectionState)
)
"""
FIRST_CONNECTION_IS_CLOSED = '() => window.recordedConnections[0].signalingState === "closed"'


def _wait_for_more_video_bytes_than(person: Page, baseline: int, deadline_seconds: int = PUMP_DEADLINE_SECONDS) -> int:
    pump_until(
        person,
        lambda: _inbound_video_bytes(person) > baseline,
        deadline_seconds,
        "no new video bytes arrived",
    )
    return _inbound_video_bytes(person)


@pytest.mark.parametrize("cut_side", [Side.GUEST, Side.HOST], ids=["guest_cut", "host_cut"])
def test_cut_signaling_socket_resumes_the_seat_while_the_video_keeps_flowing_and_nobody_hears_a_leave_or_join(
    connected_pair: ConnectedPair, cut_side: Side
) -> None:
    """CALL-10: A cut signaling socket resumes the seat; the video keeps flowing and nobody hears a leave or join."""
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

    say_in_chat(host, "sent after the answer")
    expect(guest.locator(HEARD_CHAT)).to_have_count(1)
    assert len(_frames_of(connected_pair.guest_tap, "downstream", SignalType.ANSWER)) == answers_before + 1


def test_chat_swallowed_at_the_cut_reaches_the_other_side_exactly_once_after_the_resume(
    connected_pair: ConnectedPair,
) -> None:
    guest = connected_pair.guest
    connected_pair.guest_tap.swallow_upstream = 1

    say_in_chat(guest, "lost in the cut")
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
        say_in_chat(guest, "first")
        say_in_chat(guest, "second")

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
    say_in_chat(guest, "sent after both offers")
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
        say_in_chat(host, "sent after both answers")
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
    guest, tap = connected_pair.guest, connected_pair.guest_tap
    for _ in range(CUTS_IN_A_ROW + 1):
        sockets_before_the_cut = len(tap.urls)
        connected_pair.cut(guest)
        assert len(tap.urls) == sockets_before_the_cut
        connected_pair.resume(guest)
        assert len(tap.urls) == sockets_before_the_cut + 1


def test_server_frame_delivered_twice_is_dispatched_once(connected_pair: ConnectedPair) -> None:
    guest, host = connected_pair.guest, connected_pair.host
    connected_pair.guest_tap.deliver_chats_twice = True

    say_in_chat(host, "once")
    say_in_chat(host, "marker")

    expect(guest.locator(HEARD_CHAT)).to_have_text([re.compile("once$"), re.compile("marker$")])


def test_events_of_a_replaced_socket_are_ignored(connected_pair: ConnectedPair) -> None:
    guest = connected_pair.guest
    connected_pair.cut(guest)
    connected_pair.resume(guest)

    guest.evaluate(LATE_EVENTS_OF_THE_FIRST_SOCKET)
    guest.clock.run_for(RECONNECT_DELAYS_MS[0])
    say_in_chat(guest, "still connected")

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

    say_in_chat(guest, "typed while resuming")
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
        sockets_before = guest.evaluate(SOCKETS_OPENED)
        guest.clock.run_for(delay)
        _wait_for(guest, NEW_SOCKET_IS_CLOSED, sockets_before)
        if attempt < len(RECONNECT_DELAYS_MS):
            wait_for_status(guest, _reconnecting_status(RECONNECT_DELAYS_MS[attempt]))
    wait_for_status(guest, text("de", "call.statusConnectionLost"))
    upstream_before = len(tap.upstream)
    say_in_chat(guest, "nobody hears this")

    assert len(tap.urls) == len(RECONNECT_DELAYS_MS) + 1
    assert len(tap.upstream) == upstream_before
    window_ms = RESUME_WINDOW_SECONDS * 1000
    assert window_ms - RECONNECT_DELAYS_MS[-1] <= sum(RECONNECT_DELAYS_MS) < window_ms


def test_no_signaling_url_carries_a_resume_token(connected_pair: ConnectedPair) -> None:
    guest = connected_pair.guest
    connected_pair.cut(guest)
    connected_pair.resume(guest)

    tokens = {message["resume_token"] for message in connected_pair.guest_tap.downstream if "resume_token" in message}
    assert len(tokens) == 2
    assert not any(token in url for token in tokens for url in connected_pair.guest_tap.urls)
