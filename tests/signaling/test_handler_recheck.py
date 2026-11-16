import asyncio

import pytest
import pytest_asyncio
import structlog.testing

from hopin.config.constants import (
    HOST_GRACE_SECONDS,
    POLICY_VIOLATION_CLOSE_CODE,
    SEAT_CLOSED_BY_SERVER_CODE,
    SERVER_ERROR_CLOSE_CODE,
    SESSION_ENDED_CLOSE_CODE,
    SESSION_RECHECK_SECONDS,
    ErrorReason,
    SignalType,
)
from hopin.signaling.handler import SignalingHandler
from tests.signaling.support.browsers import HeldClose, HeldWrite, Link, host_and_guest
from tests.support.signaling_fakes import account_named, disconnect_frame

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

HELD_TIMEOUT_SECONDS = 5


class ScriptedResolver:
    """Answers with the account the socket carries; a held socket waits for the test, a failing one raises."""

    def __init__(self):
        self.failing = {}
        self.held = set()
        self.consulted = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def resolve(self, websocket):
        self.consulted.append(websocket)
        if websocket in self.held:
            await self.wait_for_release()
        if websocket in self.failing:
            raise self.failing[websocket]
        return websocket.account

    async def wait_for_release(self):
        self.entered.set()
        await asyncio.wait_for(self.release.wait(), timeout=HELD_TIMEOUT_SECONDS)


@pytest.fixture
def resolver():
    return ScriptedResolver()


@pytest_asyncio.fixture
async def link(room_manager, scheduler, resolver):
    link = Link(SignalingHandler(room_manager, resolver, scheduler))
    yield link
    await link.close_all()


def pending_ticks(scheduler):
    return scheduler.pending_timers()


def ticks_due_at(scheduler, deadline):
    return [timer for timer in pending_ticks(scheduler) if timer.deadline == deadline]


async def suspend_check_of(browser, resolver, scheduler):
    """Run the tick until the resolver holds the socket's check."""
    resolver.held.add(browser.socket)
    resolver.entered.clear()
    resolver.release.clear()
    ticking = asyncio.create_task(scheduler.advance(SESSION_RECHECK_SECONDS))
    await asyncio.wait_for(resolver.entered.wait(), timeout=HELD_TIMEOUT_SECONDS)
    return ticking


async def release_check(resolver, ticking):
    resolver.release.set()
    await ticking


class TestTick:
    async def test_admitted_socket_has_one_tick_a_minute_away_and_a_refused_handshake_has_none(self, link, scheduler):
        refused = await link.open(None)

        [] = pending_ticks(scheduler)
        assert refused.closed_with == POLICY_VIOLATION_CLOSE_CODE
        await link.open(account_named("guest"))
        [tick] = pending_ticks(scheduler)
        assert tick.deadline == SESSION_RECHECK_SECONDS

    async def test_ticks_of_a_live_member_leave_the_socket_open_and_each_schedules_the_next(self, link, scheduler):
        host, _ = await host_and_guest(link)

        for passed in range(1, 4):
            await scheduler.advance(SESSION_RECHECK_SECONDS)
            assert host.socket.closed_with is None
            assert (
                sorted(timer.deadline for timer in pending_ticks(scheduler))
                == [(passed + 1) * SESSION_RECHECK_SECONDS] * 2
            )

    async def test_idle_guest_whose_sign_in_ended_is_closed_at_the_tick_and_the_host_hears_it_left(
        self, link, scheduler, room_manager
    ):
        host, guest = await host_and_guest(link)
        guest.socket.account = None

        await scheduler.advance(SESSION_RECHECK_SECONDS - 1)
        assert guest.socket.closed_with is None
        await scheduler.advance(1)

        assert guest.socket.closed_with == SESSION_ENDED_CLOSE_CODE
        [left] = host.frames(SignalType.PARTICIPANT_LEFT)
        assert left["participant_id"] == guest.peer_id
        assert room_manager.get_room(host.socket.sent[0]["room_id"]).participant_ids == [host.peer_id]

    async def test_host_whose_sign_in_ended_starts_the_grace_and_the_room_ends_a_minute_later(self, link, scheduler):
        host, guest = await host_and_guest(link)
        host.socket.account = None

        await scheduler.advance(SESSION_RECHECK_SECONDS)
        [away] = guest.frames(SignalType.HOST_AWAY)
        assert away["seconds_left"] == HOST_GRACE_SECONDS
        assert guest.frames(SignalType.ROOM_CLOSED) == []
        await scheduler.advance(HOST_GRACE_SECONDS)

        assert len(guest.frames(SignalType.ROOM_CLOSED)) == 1

    async def test_check_that_raises_at_the_tick_closes_the_socket_and_parks_the_seat_until_the_store_answers(
        self, link, scheduler, resolver, room_manager
    ):
        host, guest = await host_and_guest(link)
        resolver.failing[guest.socket] = RuntimeError("store unavailable")

        await scheduler.advance(SESSION_RECHECK_SECONDS)
        assert guest.socket.closed_with == SERVER_ERROR_CLOSE_CODE
        await guest.drop(SERVER_ERROR_CLOSE_CODE)

        assert host.frames(SignalType.PARTICIPANT_LEFT) == []
        assert guest.peer_id in room_manager.get_room(host.socket.sent[0]["room_id"]).participant_ids
        resolver.failing.clear()
        await guest.resume(account_named("guest"))
        await guest.accept_resumption()
        assert len(guest.frames(SignalType.RESUMED)) == 1
        assert host.frames(SignalType.PARTICIPANT_LEFT) == []

    async def test_session_ended_between_ticks_ends_the_socket_at_the_next_tick_and_not_before(self, link, scheduler):
        host, guest = await host_and_guest(link)
        await scheduler.advance(SESSION_RECHECK_SECONDS)
        guest.socket.account = None
        await guest.say({"type": SignalType.CHAT.value, "message": "still heard"})

        assert guest.socket.closed_with is None
        assert len(host.frames(SignalType.CHAT)) == 1
        await scheduler.advance(SESSION_RECHECK_SECONDS)

        assert guest.socket.closed_with == SESSION_ENDED_CLOSE_CODE
        assert len(host.frames(SignalType.PARTICIPANT_LEFT)) == 1

    async def test_frame_does_not_read_the_session(self, link, resolver):
        _, guest = await host_and_guest(link)
        consulted_before = list(resolver.consulted)

        await guest.say({"type": SignalType.CHAT.value, "message": "hello"})

        assert resolver.consulted == consulted_before


class TestSuspendedCheck:
    @pytest.mark.parametrize("answer", ["refused", "admitted"])
    async def test_check_suspended_across_a_drop_leaves_the_parked_seat_alone(
        self, link, scheduler, resolver, room_manager, answer
    ):
        host, guest = await host_and_guest(link)
        ticking = await suspend_check_of(guest, resolver, scheduler)
        await guest.drop(1006)
        if answer == "refused":
            guest.socket.account = None

        with structlog.testing.capture_logs() as logs:
            await release_check(resolver, ticking)

        assert guest.socket.closed_with is None
        assert [entry for entry in logs if entry["event"] == "session.expired"] == []
        assert host.frames(SignalType.PARTICIPANT_LEFT) == []
        assert guest.peer_id in room_manager.get_room(host.socket.sent[0]["room_id"]).participant_ids
        assert len(ticks_due_at(scheduler, 2 * SESSION_RECHECK_SECONDS)) == 1

    @pytest.mark.parametrize("old_handler", ["still_running", "finished"])
    @pytest.mark.parametrize("answer", ["refused", "admitted"])
    async def test_check_suspended_across_a_takeover_leaves_the_new_socket_in_its_seat(
        self, link, scheduler, resolver, room_manager, answer, old_handler
    ):
        host, guest = await host_and_guest(link)
        old_socket = guest.socket
        ticking = await suspend_check_of(guest, resolver, scheduler)
        await guest.resume()
        await guest.accept_resumption()
        if old_handler == "finished":
            old_socket.push(disconnect_frame(SEAT_CLOSED_BY_SERVER_CODE))
            await link.tasks[old_socket]
        if answer == "refused":
            old_socket.account = None
        before = len(host.frames(SignalType.PARTICIPANT_LEFT))

        with structlog.testing.capture_logs() as logs:
            await release_check(resolver, ticking)

        assert guest.socket.closed_with is None
        assert [entry for entry in logs if entry["event"] == "session.expired"] == []
        assert len(host.frames(SignalType.PARTICIPANT_LEFT)) == before
        assert guest.peer_id in room_manager.get_room(host.socket.sent[0]["room_id"]).participant_ids
        assert len(ticks_due_at(scheduler, 2 * SESSION_RECHECK_SECONDS)) == 2


class TestSuspendedEnding:
    async def test_session_end_closes_the_socket_while_another_participant_is_still_being_told_it_left(
        self, link, scheduler
    ):
        host, guest = await host_and_guest(link)
        guest.socket.account = None
        held_write = HeldWrite(host.socket)
        ticking = asyncio.create_task(scheduler.advance(SESSION_RECHECK_SECONDS))
        await held_write.reached()

        assert guest.socket.closed_with == SESSION_ENDED_CLOSE_CODE
        held_write.release.set()
        await asyncio.wait_for(ticking, timeout=HELD_TIMEOUT_SECONDS)
        [left] = host.frames(SignalType.PARTICIPANT_LEFT)
        assert left["participant_id"] == guest.peer_id

    async def test_host_back_while_its_ended_session_is_closing_is_last_heard_as_back(self, link, scheduler):
        host, guest = await host_and_guest(link)
        host.socket.account = None
        held_close = HeldClose(host.socket)
        ticking = asyncio.create_task(scheduler.advance(SESSION_RECHECK_SECONDS))
        await held_close.reached()
        await link.join("host", host.socket.sent[0]["room_id"])

        held_close.release.set()
        await asyncio.wait_for(ticking, timeout=HELD_TIMEOUT_SECONDS)

        assert guest.frames(SignalType.HOST_AWAY, SignalType.HOST_BACK)[-1]["type"] == SignalType.HOST_BACK.value

    async def test_resume_while_the_session_ending_closes_the_socket_is_refused_and_the_host_hears_it_left(
        self, link, scheduler, room_manager
    ):
        host, guest = await host_and_guest(link)
        guest.socket.account = None
        held_close = HeldClose(guest.socket)
        ticking = asyncio.create_task(scheduler.advance(SESSION_RECHECK_SECONDS))
        await held_close.reached()
        await guest.resume(account_named("guest"))

        held_close.release.set()
        await ticking

        assert guest.frames(SignalType.RESUMED) == []
        assert guest.frames(SignalType.ERROR)[-1]["reason"] == ErrorReason.RESUME_REFUSED.value
        assert len(host.frames(SignalType.PARTICIPANT_LEFT)) == 1
        assert guest.peer_id not in room_manager.get_room(host.socket.sent[0]["room_id"]).participant_ids


class TestOtherEnds:
    async def test_socket_closed_normally_is_not_ended_again_when_the_tick_passes(self, link, scheduler):
        _, guest = await host_and_guest(link)
        await guest.drop(1000)
        guest.socket.account = None
        assert len(pending_ticks(scheduler)) == 1

        with structlog.testing.capture_logs() as logs:
            await scheduler.advance(SESSION_RECHECK_SECONDS)

        assert guest.socket.closed_with is None
        assert [entry for entry in logs if entry["event"] == "session.expired"] == []

    async def test_tick_that_fired_before_the_socket_closed_but_ran_after_does_nothing(self, link, scheduler, resolver):
        _, guest = await host_and_guest(link)
        [tick] = pending_ticks(scheduler)[1:]
        tick.pending = False
        await guest.drop(1000)
        guest.socket.account = None
        consulted_before = list(resolver.consulted)

        await tick.callback()

        assert guest.socket.closed_with is None
        assert resolver.consulted == consulted_before
        assert len(ticks_due_at(scheduler, 2 * SESSION_RECHECK_SECONDS)) == 0

    async def test_resumed_seat_is_checked_through_its_new_socket(self, link, scheduler):
        host, guest = await host_and_guest(link)
        await guest.resume()
        await guest.accept_resumption()
        guest.socket.account = None

        await scheduler.advance(SESSION_RECHECK_SECONDS)

        assert guest.socket.closed_with == SESSION_ENDED_CLOSE_CODE
        assert len(host.frames(SignalType.PARTICIPANT_LEFT)) == 1

    async def test_tick_of_a_guest_in_an_ended_room_does_nothing(self, link, scheduler):
        host, guest = await host_and_guest(link)
        await host.say({"type": SignalType.END_ROOM.value})
        guest.socket.account = None
        closed_with = guest.socket.closed_with

        with structlog.testing.capture_logs() as logs:
            await scheduler.advance(SESSION_RECHECK_SECONDS)

        assert guest.socket.closed_with == closed_with
        assert [entry for entry in logs if entry["event"] == "session.expired"] == []
        assert host.frames(SignalType.PARTICIPANT_LEFT) == []
