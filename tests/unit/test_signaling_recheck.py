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
    SignalType,
)
from hopin.signaling import handler as handler_module
from hopin.signaling.handler import SignalingHandler
from tests.unit.conftest import account_named, disconnect_frame, text_frame
from tests.unit.test_signaling_resume import Link, host_and_guest

pytestmark = pytest.mark.asyncio

HELD_TIMEOUT_SECONDS = 5


class ScriptedResolver:
    """Answers with the account the socket carries; a held socket waits for the test, a failing one raises.

    A socket held as admitted gets the answer it had when the check began, once.
    """

    def __init__(self):
        self.failing = {}
        self.held = set()
        self.held_admitted = set()
        self.consulted = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def resolve(self, websocket):
        self.consulted.append(websocket)
        if websocket in self.held_admitted:
            self.held_admitted.discard(websocket)
            account = websocket.account
            await self.wait_for_release()
            return account
        if websocket in self.held:
            await self.wait_for_release()
        if websocket in self.failing:
            raise self.failing[websocket]
        return websocket.account

    async def wait_for_release(self):
        self.entered.set()
        await asyncio.wait_for(self.release.wait(), timeout=HELD_TIMEOUT_SECONDS)


class HeldSessionEndedLog:
    """Wraps the handler's logger and holds the session_ended entry until the test releases it."""

    def __init__(self, logger):
        self.logger = logger
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    def __getattr__(self, name):
        return getattr(self.logger, name)

    async def ainfo(self, event, **fields):
        if event == "session_ended":
            self.entered.set()
            await asyncio.wait_for(self.release.wait(), timeout=HELD_TIMEOUT_SECONDS)
        await self.logger.ainfo(event, **fields)


@pytest.fixture
def held_log(monkeypatch):
    held = HeldSessionEndedLog(handler_module.logger)
    monkeypatch.setattr(handler_module, "logger", held)
    return held


@pytest.fixture
def resolver():
    return ScriptedResolver()


@pytest_asyncio.fixture
async def link(room_manager, scheduler, resolver):
    link = Link(SignalingHandler(room_manager, resolver, scheduler))
    yield link
    await link.close_all()


def pending_ticks(scheduler):
    return [timer for timer in scheduler._timers if timer.pending]


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

    async def test_check_that_raises_at_the_tick_closes_the_socket_releases_the_seat_and_reaches_the_scheduler(
        self, link, scheduler, resolver
    ):
        host, guest = await host_and_guest(link)
        resolver.failing[guest.socket] = RuntimeError("store unavailable")

        with pytest.raises(RuntimeError, match="store unavailable"):
            await scheduler.advance(SESSION_RECHECK_SECONDS)

        assert guest.socket.closed_with == SERVER_ERROR_CLOSE_CODE
        assert len(host.frames(SignalType.PARTICIPANT_LEFT)) == 1
        assert len(ticks_due_at(scheduler, 2 * SESSION_RECHECK_SECONDS)) == 1


class TestFrame:
    async def test_frame_after_the_sign_in_ended_closes_the_socket_and_is_not_handled(self, link):
        host, guest = await host_and_guest(link)
        guest.socket.account = None

        await guest.say({"type": SignalType.CHAT.value, "message": "still here"})

        assert guest.socket.closed_with == SESSION_ENDED_CLOSE_CODE
        assert host.frames(SignalType.CHAT) == []
        assert len(host.frames(SignalType.PARTICIPANT_LEFT)) == 1

    async def test_check_that_raises_before_a_frame_closes_the_socket_releases_the_seat_and_leaves_the_handler(
        self, link, resolver
    ):
        host, guest = await host_and_guest(link)
        resolver.failing[guest.socket] = RuntimeError("store unavailable")
        guest.socket.push({"type": "websocket.receive", "text": '{"type": "chat", "message": "hi", "seq": 1}'})

        with pytest.raises(RuntimeError, match="store unavailable"):
            await link.tasks[guest.socket]

        assert guest.socket.closed_with == SERVER_ERROR_CLOSE_CODE
        assert host.frames(SignalType.CHAT) == []
        assert len(host.frames(SignalType.PARTICIPANT_LEFT)) == 1


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
        assert [entry for entry in logs if entry["event"] == "session_ended"] == []
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
        assert [entry for entry in logs if entry["event"] == "session_ended"] == []
        assert len(host.frames(SignalType.PARTICIPANT_LEFT)) == before
        assert guest.peer_id in room_manager.get_room(host.socket.sent[0]["room_id"]).participant_ids
        assert len(ticks_due_at(scheduler, 2 * SESSION_RECHECK_SECONDS)) == 2


class TestSuspendedEnding:
    async def test_session_ending_suspended_in_its_log_across_a_takeover_leaves_the_new_socket_in_its_seat(
        self, link, scheduler, room_manager, held_log
    ):
        host, guest = await host_and_guest(link)
        guest.socket.account = None
        ticking = asyncio.create_task(scheduler.advance(SESSION_RECHECK_SECONDS))
        await asyncio.wait_for(held_log.entered.wait(), timeout=HELD_TIMEOUT_SECONDS)
        await guest.resume(account_named("guest"))
        await guest.accept_resumption()

        held_log.release.set()
        await ticking

        assert guest.socket.closed_with is None
        assert host.frames(SignalType.PARTICIPANT_LEFT) == []
        assert guest.peer_id in room_manager.get_room(host.socket.sent[0]["room_id"]).participant_ids

    @pytest.mark.parametrize(
        ("tick_check", "close_code"),
        [("refuses", SESSION_ENDED_CLOSE_CODE), ("raises", SERVER_ERROR_CLOSE_CODE)],
    )
    async def test_frame_admitted_after_a_tick_ended_the_connection_is_not_handled_and_the_socket_gets_the_tick_close(
        self, link, scheduler, resolver, held_log, tick_check, close_code
    ):
        host, guest = await host_and_guest(link)
        resolver.held_admitted.add(guest.socket)
        guest.socket.push(text_frame({"type": SignalType.CHAT.value, "message": "late"}, seq=1))
        await asyncio.wait_for(resolver.entered.wait(), timeout=HELD_TIMEOUT_SECONDS)
        if tick_check == "refuses":
            guest.socket.account = None
        else:
            resolver.failing[guest.socket] = RuntimeError("store unavailable")
        ticking = asyncio.create_task(scheduler.advance(SESSION_RECHECK_SECONDS))
        await asyncio.wait_for(held_log.entered.wait(), timeout=HELD_TIMEOUT_SECONDS)

        resolver.release.set()
        held_log.release.set()
        if tick_check == "raises":
            with pytest.raises(RuntimeError, match="store unavailable"):
                await asyncio.wait_for(ticking, timeout=HELD_TIMEOUT_SECONDS)
        else:
            await asyncio.wait_for(ticking, timeout=HELD_TIMEOUT_SECONDS)

        assert guest.socket.closed_with == close_code
        guest.socket.push(disconnect_frame(close_code))
        await asyncio.wait_for(link.tasks[guest.socket], timeout=HELD_TIMEOUT_SECONDS)
        assert host.frames(SignalType.CHAT) == []


class TestOtherEnds:
    async def test_socket_closed_normally_is_not_ended_again_when_the_tick_passes(self, link, scheduler):
        _, guest = await host_and_guest(link)
        await guest.drop(1000)
        guest.socket.account = None
        assert len(pending_ticks(scheduler)) == 1

        with structlog.testing.capture_logs() as logs:
            await scheduler.advance(SESSION_RECHECK_SECONDS)

        assert guest.socket.closed_with is None
        assert [entry for entry in logs if entry["event"] == "session_ended"] == []

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
        assert [entry for entry in logs if entry["event"] == "session_ended"] == []
        assert host.frames(SignalType.PARTICIPANT_LEFT) == []
