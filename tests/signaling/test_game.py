import asyncio

import pytest
import pytest_asyncio
import structlog.testing

from hopin.config.constants import (
    GAME_HISTORY_LIMIT,
    GAME_RUN_DEADLINE_SECONDS,
    HOST_GRACE_SECONDS,
    MAX_PARTICIPANTS_PER_ROOM,
    ROOM_ID_LENGTH,
    GamePhase,
    GameRefusal,
    SignalType,
)
from hopin.rooms.manager import RoomManager
from hopin.signaling.game import Question, Refused
from hopin.signaling.handler import SignalingHandler
from tests.signaling.support.browsers import Browser, Link
from tests.support.signaling_fakes import (
    ARRIVAL_CAP_SECONDS,
    DEFAULT_QUESTION,
    ROOM_LIMIT,
    SocketAccountResolver,
    account_named,
    text_frame,
)

pytestmark = pytest.mark.asyncio

START = {"type": SignalType.GAME_START.value}
END_ROOM = {"type": SignalType.END_ROOM.value}
GAME_TYPES = frozenset({SignalType.GAME_PHASE, SignalType.GAME_LINE, SignalType.GAME_REFUSED})
THINKING = {"type": SignalType.GAME_PHASE.value, "phase": GamePhase.THINKING.value}
IDLE = {"type": SignalType.GAME_PHASE.value, "phase": GamePhase.IDLE.value}
YIELD_CAP = 100


def line(text=DEFAULT_QUESTION):
    return {"type": SignalType.GAME_LINE.value, "text": text}


def refused(reason, **fields):
    return {"type": SignalType.GAME_REFUSED.value, "reason": reason.value, **fields}


def without_seq(frame):
    return {key: value for key, value in frame.items() if key != "seq"}


def game_frames(browser):
    """The game frames the browser was sent, over all its sockets, without their sequence numbers."""
    return [without_seq(frame) for frame in browser.frames(*GAME_TYPES)]


def sequenced_game_frames(browser):
    return [(frame["seq"], without_seq(frame)) for frame in browser.frames(*GAME_TYPES)]


async def sent(browser, frame, times=1):
    """Wait until the browser has been sent the frame `times` times."""
    await browser.socket.wait_until(lambda: game_frames(browser).count(frame) >= times)


async def sent_to_all(browsers, frame, times=1):
    for browser in browsers:
        await sent(browser, frame, times)


async def wait_within_cap(event):
    async with asyncio.timeout(ARRIVAL_CAP_SECONDS):
        await event.wait()


async def shut_down_within_cap(handler):
    """Shut down, failing instead of hanging when a run's ask is never cancelled."""
    async with asyncio.timeout(ARRIVAL_CAP_SECONDS):
        await handler.shut_down()


@pytest_asyncio.fixture
async def link(handler, game_master):
    link = Link(handler)
    yield link
    await link.close_all()
    game_master.release_every_hold()
    await shut_down_within_cap(handler)


async def yield_until(condition):
    """Let the other tasks run until the condition holds; for a step that no frame announces."""
    for _ in range(YIELD_CAP):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("the condition never held")


async def call_of(link, *names):
    host = await link.join(names[0])
    room_id = host.socket.sent[0]["room_id"]
    return [host, *[await link.join(name, room_id) for name in names[1:]]]


async def start_and_hold(game_master, browser, **hold_options):
    held = game_master.hold_next(**hold_options)
    await browser.say(START)
    await wait_within_cap(held.started)
    return held


class TestOneRun:
    async def test_every_seat_sees_thinking_then_the_line_then_idle(self, link):
        call = await call_of(link, "host", "guest")

        await call[1].say(START)

        await sent_to_all(call, IDLE)
        for browser in call:
            assert game_frames(browser) == [THINKING, line(), IDLE]

    async def test_the_run_asks_on_the_starters_socket_with_no_earlier_question(self, link, game_master):
        _, guest = await call_of(link, "host", "guest")

        await guest.say(START)
        await sent(guest, IDLE)

        [call] = game_master.calls
        assert (call.starter, call.questions) == (guest.socket, ())

    async def test_the_other_sees_thinking_while_the_run_is_in_flight(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")

        held = await start_and_hold(game_master, guest)
        await sent(host, THINKING)

        assert game_frames(host) == [THINKING]
        held.release.set()
        await sent(host, IDLE)

    async def test_other_participant_sees_thinking_then_idle_while_only_the_starter_gets_the_refusal(
        self, link, game_master
    ):
        host, guest = await call_of(link, "host", "guest")
        game_master.answer_next(Refused(GameRefusal.NO_ACCESS))

        await guest.say(START)
        await sent_to_all([host, guest], IDLE)

        assert game_frames(guest) == [THINKING, refused(GameRefusal.NO_ACCESS), IDLE]
        assert game_frames(host) == [THINKING, IDLE]

    async def test_a_busy_refusal_carries_its_seconds(self, link, game_master):
        [host] = await call_of(link, "host")
        game_master.answer_next(Refused(GameRefusal.BUSY, retry_after_seconds=17))

        await host.say(START)
        await sent(host, IDLE)

        assert game_frames(host) == [THINKING, refused(GameRefusal.BUSY, retry_after_seconds=17), IDLE]

    async def test_a_game_master_that_raises_is_unavailable_to_the_starter_and_logged_by_its_type_only(
        self, link, game_master
    ):
        [host] = await call_of(link, "host")
        game_master.answer_next(RuntimeError("the secret text of a failure"))

        with structlog.testing.capture_logs() as logs:
            await host.say(START)
            await sent(host, IDLE)

        assert game_frames(host) == [THINKING, refused(GameRefusal.UNAVAILABLE), IDLE]
        assert "the secret text of a failure" not in repr(logs)
        assert {"event": "game_master_raised", "log_level": "error", "error": "RuntimeError"} in logs

    async def test_the_run_is_logged_with_its_room_outcome_and_duration_only(self, link, game_master, scheduler):
        [host] = await call_of(link, "host")
        held = await start_and_hold(game_master, host)
        await scheduler.advance(3)

        with structlog.testing.capture_logs() as logs:
            held.release.set()
            await sent(host, IDLE)

        [run] = [log for log in logs if log["event"] == "game_master_run"]
        assert run == {
            "event": "game_master_run",
            "log_level": "info",
            "room_id": host.socket.sent[0]["room_id"],
            "outcome": "question",
            "duration_ms": 3000,
        }

    async def test_a_second_tap_asks_the_next_question_after_the_first(self, link, game_master):
        [host] = await call_of(link, "host")
        game_master.answer_next(Question("Erste Frage?"), Question("Zweite Frage?"))

        await host.say(START)
        await sent(host, IDLE)
        await host.say(START)
        await sent(host, IDLE, times=2)

        assert game_frames(host)[3:] == [THINKING, line("Zweite Frage?"), IDLE]
        assert [call.questions for call in game_master.calls] == [(), ("Erste Frage?",)]

    async def test_a_run_sends_only_the_newest_questions_of_the_call(self, link, game_master):
        [host] = await call_of(link, "host")
        asked = [f"Frage {number}?" for number in range(GAME_HISTORY_LIMIT + 1)]
        game_master.answer_next(*[Question(text) for text in asked])

        for runs in range(1, len(asked) + 1):
            await host.say(START)
            await sent(host, IDLE, times=runs)
        await host.say(START)
        await sent(host, line())

        assert game_master.calls[-1].questions == tuple(asked[-GAME_HISTORY_LIMIT:])

    async def test_a_start_outside_a_room_is_ignored(self, link, game_master):
        loner = Browser(link, "loner")
        loner.sockets.append(await link.open(account_named("loner")))

        await loner.say(START)

        assert (game_frames(loner), game_master.calls) == ([], [])

    async def test_a_call_alone_gets_its_line(self, link):
        [host] = await call_of(link, "host")

        await host.say(START)
        await sent(host, IDLE)

        assert game_frames(host) == [THINKING, line(), IDLE]


class TestOneRunPerRoom:
    async def test_a_second_start_in_flight_is_refused_to_its_sender_only(self, link, game_master):
        host, guest, third = await call_of(link, "host", "guest", "third")
        held = await start_and_hold(game_master, guest)

        await third.say(START)
        await sent(third, refused(GameRefusal.ALREADY_ANSWERING))

        assert game_frames(third) == [THINKING, refused(GameRefusal.ALREADY_ANSWERING)]
        assert game_frames(guest) == game_frames(host) == [THINKING]
        assert len(game_master.calls) == 1
        held.release.set()
        await sent_to_all([host, guest, third], IDLE)

    async def test_two_rooms_run_at_once(self, link, game_master):
        [first] = await call_of(link, "first")
        [second] = await call_of(link, "second")
        held = await start_and_hold(game_master, first)

        await second.say(START)
        await sent(second, IDLE)

        assert game_frames(second) == [THINKING, line(), IDLE]
        held.release.set()
        await sent(first, IDLE)

    async def test_a_start_while_the_deadlines_cleanup_is_held_is_refused_and_idle_precedes_the_next_thinking(
        self, link, game_master, scheduler
    ):
        host, guest = await call_of(link, "host", "guest")
        held = await start_and_hold(game_master, guest, hold_cleanup=True)
        await scheduler.advance(GAME_RUN_DEADLINE_SECONDS)
        await wait_within_cap(held.cleanup_started)

        await host.say(START)
        await sent(host, refused(GameRefusal.ALREADY_ANSWERING))
        held.cleanup_release.set()
        await sent_to_all([host, guest], IDLE)
        await host.say(START)
        await sent_to_all([host, guest], IDLE, times=2)

        assert game_frames(guest) == [THINKING, refused(GameRefusal.UNAVAILABLE), IDLE, THINKING, line(), IDLE]
        assert_idle_comes_before_the_next_thinking([host, guest])

    async def test_a_start_while_the_lines_delivery_is_held_at_a_seat_is_refused_and_idle_precedes_the_next_thinking(
        self, link, game_master
    ):
        host, guest, third = await call_of(link, "host", "guest", "third")
        held = await start_and_hold(game_master, guest)
        await sent(third, THINKING)
        gate = asyncio.Event()
        third.socket.send_gate = gate

        held.release.set()
        await sent(host, line())
        await host.say(START)
        await sent(host, refused(GameRefusal.ALREADY_ANSWERING))
        gate.set()
        await sent_to_all([host, guest, third], IDLE)
        await host.say(START)
        await sent_to_all([host, guest, third], IDLE, times=2)

        assert len(game_master.calls) == 2
        assert_idle_comes_before_the_next_thinking([host, guest, third])


def assert_idle_comes_before_the_next_thinking(browsers):
    for browser in browsers:
        phases = [(seq, frame["phase"]) for seq, frame in sequenced_game_frames(browser) if "phase" in frame]
        assert [phase for _, phase in phases][:3] == ["thinking", "idle", "thinking"]
        assert [seq for seq, _ in phases] == sorted(seq for seq, _ in phases)


class TestDeadline:
    async def test_a_run_past_its_deadline_is_cancelled_unavailable_to_the_starter_and_idle_to_all(
        self, link, game_master, scheduler
    ):
        host, guest = await call_of(link, "host", "guest")
        await start_and_hold(game_master, guest)

        await scheduler.advance(GAME_RUN_DEADLINE_SECONDS)
        await sent_to_all([host, guest], IDLE)

        [call] = game_master.calls
        assert (call.cancelled, call.finished) == (True, True)
        assert game_frames(guest) == [THINKING, refused(GameRefusal.UNAVAILABLE), IDLE]
        assert game_frames(host) == [THINKING, IDLE]

    async def test_a_run_answered_in_time_leaves_no_deadline_behind(self, link, scheduler):
        [host] = await call_of(link, "host")

        await host.say(START)
        await sent(host, IDLE)

        assert all(timer.deadline != GAME_RUN_DEADLINE_SECONDS for timer in scheduler.pending_timers())


class TestRoomEnd:
    async def test_ending_the_call_while_asking_cancels_the_run_and_sends_no_game_frame(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")
        await start_and_hold(game_master, guest)
        [call] = game_master.calls

        with structlog.testing.capture_logs() as logs:
            await host.say(END_ROOM)
            await yield_until(lambda: call.finished)
            assert call.cancelled
            await shut_down_within_cap(link.handler)

        assert_no_driver_failed(logs)
        assert_no_game_frame_after_room_closed([host, guest])

    async def test_ending_the_call_while_the_line_is_held_at_a_seat_sends_no_game_frame_after_room_closed(
        self, link, game_master
    ):
        host, guest, third = await call_of(link, "host", "guest", "third")
        held = await start_and_hold(game_master, host)
        await sent(third, THINKING)
        gate = asyncio.Event()
        guest.socket.send_gate = gate
        held.release.set()
        await sent(host, line())

        with structlog.testing.capture_logs() as logs:
            push_unsettled(host, END_ROOM)
            await host.socket.wait_until(lambda: host.frames(SignalType.ROOM_CLOSED))
            gate.set()
            await shut_down_within_cap(link.handler)

        assert_no_driver_failed(logs)
        assert_no_game_frame_after_room_closed([host, guest, third])
        assert line() in game_frames(guest)
        assert line() not in game_frames(third)

    async def test_the_hosts_grace_ending_the_call_mid_run_cancels_the_run_and_sends_no_game_frame(
        self, link, game_master, scheduler
    ):
        host, guest = await call_of(link, "host", "guest")
        await start_and_hold(game_master, guest)
        [call] = game_master.calls

        with structlog.testing.capture_logs() as logs:
            await host.drop(code=1000)
            await scheduler.advance(HOST_GRACE_SECONDS)
            await yield_until(lambda: call.finished)
            assert call.cancelled
            await shut_down_within_cap(link.handler)

        assert_no_driver_failed(logs)
        assert guest.frames(SignalType.ROOM_CLOSED)
        assert_no_game_frame_after_room_closed([guest])

    async def test_a_call_ended_in_the_same_instant_as_the_tap_gets_no_game_frame(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")

        push_unsettled(host, START)
        push_unsettled(host, END_ROOM)
        await host.socket.wait_until(lambda: host.frames(SignalType.ROOM_CLOSED))
        await shut_down_within_cap(link.handler)

        assert game_frames(host) == game_frames(guest) == []
        assert game_master.calls == []


class RoomIdUsedAgain(RoomManager):
    """Hands every new room the same id, as chance may hand a later call the id of one that ended."""

    def _generate_room_id(self):
        return "r" * ROOM_ID_LENGTH


class TestARoomIdUsedAgain:
    @pytest.fixture
    def room_manager(self):
        return RoomIdUsedAgain(max_participants_per_room=ROOM_LIMIT)

    async def test_a_new_call_under_an_ended_calls_room_id_starts_with_no_earlier_question(self, link, game_master):
        [host] = await call_of(link, "host")
        await host.say(START)
        await sent(host, IDLE)
        await host.say(END_ROOM)

        [again] = await call_of(link, "again")
        await again.say(START)
        await sent(again, IDLE)

        assert again.socket.sent[0]["room_id"] == host.socket.sent[0]["room_id"]
        assert game_master.calls[-1].questions == ()


def assert_no_driver_failed(logs):
    assert [log for log in logs if log["event"] == "game_driver_failed"] == []


def push_unsettled(browser, data):
    """Hand the handler the browser's next frame without waiting for the handler to go idle."""
    browser.sent_count += 1
    browser.socket.push(text_frame(data, seq=browser.sent_count))


def assert_no_game_frame_after_room_closed(browsers):
    for browser in browsers:
        frames = [frame for socket in browser.sockets for frame in socket.sent]
        [closed] = [frame["seq"] for frame in frames if frame["type"] == SignalType.ROOM_CLOSED.value]
        assert [frame for frame in frames if frame["type"] in GAME_TYPES and frame["seq"] > closed] == []


class TestShutdown:
    async def test_shutting_down_cancels_a_held_run_and_waits_for_it(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")
        await start_and_hold(game_master, guest)

        await shut_down_within_cap(link.handler)

        [call] = game_master.calls
        assert (call.cancelled, call.finished) == (True, True)
        assert game_frames(host) == game_frames(guest) == [THINKING]

    async def test_shutting_down_waits_for_a_run_whose_cleanup_outlives_its_room(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")
        held = await start_and_hold(game_master, guest, hold_cleanup=True)
        await host.say(END_ROOM)
        await wait_within_cap(held.cleanup_started)

        asyncio.get_running_loop().call_soon(held.cleanup_release.set)
        await shut_down_within_cap(link.handler)

        assert game_master.calls[0].finished


async def broken_write(_text):
    raise RuntimeError("a write that broke")


def driver_failed(logs):
    return {"event": "game_driver_failed", "log_level": "error", "error": "RuntimeError"} in logs


class TestBrokenWrite:
    async def test_a_write_that_fails_at_one_seat_leaves_the_line_and_idle_to_the_others_and_is_logged_by_its_type(
        self, link, game_master
    ):
        host, guest, third = await call_of(link, "host", "guest", "third")
        held = await start_and_hold(game_master, host)
        await sent(third, THINKING)
        guest.socket.send_text = broken_write

        with structlog.testing.capture_logs() as logs:
            held.release.set()
            await sent_to_all([host, third], IDLE)
            await yield_until(lambda: driver_failed(logs))
        await host.say(START)
        await sent(host, IDLE, times=2)

        assert game_frames(host) == game_frames(third) == [THINKING, line(), IDLE, THINKING, line(), IDLE]
        assert "a write that broke" not in repr(logs)

    async def test_a_write_that_fails_while_announcing_thinking_leaves_the_run_the_rooms_until_it_ends(
        self, link, game_master
    ):
        host, guest, third = await call_of(link, "host", "guest", "third")
        guest.socket.send_text = broken_write

        with structlog.testing.capture_logs() as logs:
            held = await start_and_hold(game_master, host)
            await sent(third, THINKING)
            await third.say(START)
            await sent(third, refused(GameRefusal.ALREADY_ANSWERING))
            held.release.set()
            await sent_to_all([host, third], IDLE)
            await yield_until(lambda: driver_failed(logs))

        assert len(game_master.calls) == 1
        assert game_frames(host) == [THINKING, line(), IDLE]
        assert game_frames(third) == [THINKING, refused(GameRefusal.ALREADY_ANSWERING), line(), IDLE]

    async def test_a_write_that_fails_while_announcing_thinking_leaves_the_run_to_shutdown(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")
        guest.socket.send_text = broken_write
        await start_and_hold(game_master, host)
        await sent(host, THINKING)

        await shut_down_within_cap(link.handler)

        [call] = game_master.calls
        assert (call.cancelled, call.finished) == (True, True)


class TestSeatsComingAndGoing:
    async def test_the_starter_leaving_mid_run_leaves_the_line_to_the_rest(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")
        held = await start_and_hold(game_master, guest)

        await guest.drop(code=1000)
        held.release.set()
        await sent(host, IDLE)

        assert game_frames(host) == [THINKING, line(), IDLE]

    async def test_a_refusal_for_a_starter_who_left_is_dropped(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")
        game_master.answer_next(Refused(GameRefusal.NO_ACCESS))
        held = await start_and_hold(game_master, guest)

        await guest.drop(code=1000)
        held.release.set()
        await sent(host, IDLE)

        assert game_frames(host) == [THINKING, IDLE]

    async def test_a_resume_mid_run_replays_the_line_and_ends_in_the_rooms_phase(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")
        held = await start_and_hold(game_master, host)
        await sent(guest, THINKING)

        await guest.drop()
        held.release.set()
        await sent(host, IDLE)
        resumed = await guest.resume()
        await resumed.wait_until(lambda: IDLE in [without_seq(frame) for frame in resumed.sent])

        replayed = [without_seq(frame) for frame in resumed.sent if frame["type"] in GAME_TYPES]
        assert replayed == [line(), IDLE]

    async def test_a_resume_while_thinking_ends_in_thinking(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")
        await guest.drop()
        held = await start_and_hold(game_master, host)
        await sent(host, THINKING)

        resumed = await guest.resume()

        assert [without_seq(frame) for frame in resumed.sent if frame["type"] in GAME_TYPES] == [THINKING]
        held.release.set()
        await sent(guest, IDLE)

    async def test_a_late_joiner_mid_run_sees_thinking_then_the_line(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")
        held = await start_and_hold(game_master, guest)

        late = await link.join("late", host.socket.sent[0]["room_id"])
        await sent(late, THINKING)
        held.release.set()
        await sent(late, IDLE)

        assert game_frames(late) == [THINKING, line(), IDLE]

    async def test_a_seat_joining_as_the_run_starts_is_told_thinking_once(self, link, game_master, room_manager):
        host = await link.join("host")
        room_id = host.socket.sent[0]["room_id"]
        joining = asyncio.Event()
        guest = Browser(link, "guest")
        guest.sockets.append(await link.open(account_named("guest"), send_gate=joining))
        guest.socket.push(text_frame({"type": SignalType.JOIN.value, "room_id": room_id}))
        await yield_until(lambda: len(room_manager.get_room(room_id).participant_ids) == 2)

        held = await start_and_hold(game_master, host)
        joining.set()
        await link.settle(guest.socket)
        held.release.set()
        await sent(guest, IDLE)

        assert game_frames(guest) == [THINKING, line(), IDLE]

    async def test_a_joiner_after_the_run_sees_no_phase(self, link):
        host, _ = await call_of(link, "host", "guest")
        await host.say(START)
        await sent(host, IDLE)

        late = await link.join("late", host.socket.sent[0]["room_id"])

        assert game_frames(late) == []


async def test_six_seats_get_the_line(scheduler, game_master):
    handler = SignalingHandler(
        RoomManager(max_participants_per_room=MAX_PARTICIPANTS_PER_ROOM),
        SocketAccountResolver(),
        scheduler,
        game_master,
    )
    link = Link(handler)
    call = await call_of(link, *[f"person{number}" for number in range(MAX_PARTICIPANTS_PER_ROOM)])

    await call[3].say(START)
    await sent_to_all(call, IDLE)

    assert all(game_frames(browser) == [THINKING, line(), IDLE] for browser in call)
    await link.close_all()
    await shut_down_within_cap(handler)
