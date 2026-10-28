import asyncio
import json

import pytest
import pytest_asyncio
import structlog.testing

from hopin.config.constants import (
    HOST_GRACE_SECONDS,
    MAX_PARTICIPANTS_PER_ROOM,
    ROOM_ID_LENGTH,
    SignalType,
)
from hopin.game.quiz import NEXT_QUESTION_REQUEST, QUIZ_ROUNDS, REVEAL_REQUEST, Speaker, Turn
from hopin.game.table import Line, Refused
from hopin.game.vocabulary import GAME_RUN_DEADLINE_SECONDS, GamePhase, GameRefusal, GameSignal
from hopin.rooms.manager import RoomManager
from hopin.signaling.handler import SignalingHandler
from tests.game.support.fake_game_master import DEFAULT_QUESTION
from tests.signaling.support.browsers import Browser, Link
from tests.support.signaling_fakes import (
    ARRIVAL_CAP_SECONDS,
    ROOM_LIMIT,
    SocketAccountResolver,
    account_named,
    text_frame,
)

pytestmark = pytest.mark.asyncio

START = {"type": GameSignal.GAME_START.value}
REVEAL = {"type": GameSignal.GAME_REVEAL.value}
END_ROOM = {"type": SignalType.END_ROOM.value}
GAME_TYPES = frozenset({GameSignal.GAME_PHASE, GameSignal.GAME_LINE, GameSignal.GAME_REFUSED})
IDLE = {"type": GameSignal.GAME_PHASE.value, "phase": GamePhase.IDLE.value}
OPENING = (Turn(Speaker.HOPIN, NEXT_QUESTION_REQUEST),)
REVEAL_LINE = "Lissabon."
YIELD_CAP = 100


def quiz_phase(phase, starter):
    return {
        "type": GameSignal.GAME_PHASE.value,
        "phase": phase.value,
        "round": 1,
        "rounds": QUIZ_ROUNDS,
        "starter": starter.peer_id,
    }


def thinking(starter):
    return quiz_phase(GamePhase.THINKING, starter)


def answering(starter):
    return quiz_phase(GamePhase.ANSWERING, starter)


def question(text=DEFAULT_QUESTION):
    return {"type": GameSignal.GAME_LINE.value, "text": text, "round": 1}


def line(text):
    return {"type": GameSignal.GAME_LINE.value, "text": text}


def asked(starter, text=DEFAULT_QUESTION):
    """What every seat sees of a question the starter's tap asked."""
    return [thinking(starter), question(text), answering(starter)]


def refused(reason, **fields):
    return {"type": GameSignal.GAME_REFUSED.value, "reason": reason.value, **fields}


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


async def open_a_question(call, starter):
    """The starter's tap, until every seat of the call has the question open."""
    await starter.say(START)
    await sent_to_all(call, answering(starter))


class TestOneRun:
    async def test_every_seat_sees_thinking_then_the_question_then_answering_with_round_and_starter(self, link):
        call = await call_of(link, "host", "guest")

        await call[1].say(START)

        await sent_to_all(call, answering(call[1]))
        for browser in call:
            assert game_frames(browser) == asked(call[1])

    async def test_the_run_asks_on_the_starters_socket_for_question_one(self, link, game_master):
        call = await call_of(link, "host", "guest")

        await open_a_question(call, call[1])

        [run] = game_master.calls
        assert (run.starter, run.transcript) == (call[1].socket, OPENING)

    async def test_the_other_sees_thinking_while_the_run_is_in_flight(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")

        held = await start_and_hold(game_master, guest)
        await sent(host, thinking(guest))

        assert game_frames(host) == [thinking(guest)]
        held.release.set()
        await sent(host, answering(guest))

    async def test_other_participant_sees_thinking_then_idle_while_only_the_starter_gets_the_refusal(
        self, link, game_master
    ):
        host, guest = await call_of(link, "host", "guest")
        game_master.answer_next(Refused(GameRefusal.NO_ACCESS))

        await guest.say(START)
        await sent_to_all([host, guest], IDLE)

        assert game_frames(guest) == [thinking(guest), refused(GameRefusal.NO_ACCESS), IDLE]
        assert game_frames(host) == [thinking(guest), IDLE]

    async def test_a_busy_refusal_carries_its_seconds(self, link, game_master):
        [host] = await call_of(link, "host")
        game_master.answer_next(Refused(GameRefusal.BUSY, retry_after_seconds=17))

        await host.say(START)
        await sent(host, IDLE)

        assert game_frames(host) == [thinking(host), refused(GameRefusal.BUSY, retry_after_seconds=17), IDLE]

    async def test_a_game_master_that_raises_is_unavailable_to_the_starter_and_logged_by_its_type_only(
        self, link, game_master
    ):
        [host] = await call_of(link, "host")
        game_master.answer_next(RuntimeError("the secret text of a failure"))

        with structlog.testing.capture_logs() as logs:
            await host.say(START)
            await sent(host, IDLE)

        assert game_frames(host) == [thinking(host), refused(GameRefusal.UNAVAILABLE), IDLE]
        assert "the secret text of a failure" not in repr(logs)
        assert {"event": "game_master_raised", "log_level": "error", "error": "RuntimeError"} in logs

    async def test_the_run_is_logged_with_its_room_outcome_and_duration_only(self, link, game_master, scheduler):
        [host] = await call_of(link, "host")
        held = await start_and_hold(game_master, host)
        await scheduler.advance(3)

        with structlog.testing.capture_logs() as logs:
            held.release.set()
            await sent(host, answering(host))

        [run] = [log for log in logs if log["event"] == "game_master_run"]
        assert run == {
            "event": "game_master_run",
            "log_level": "info",
            "room_id": host.socket.sent[0]["room_id"],
            "outcome": "line",
            "duration_ms": 3000,
        }

    @pytest.mark.parametrize("frame", [START, REVEAL], ids=["start", "reveal"])
    async def test_a_frame_from_outside_a_room_is_ignored(self, link, game_master, frame):
        loner = Browser(link, "loner")
        loner.sockets.append(await link.open(account_named("loner")))

        await loner.say(frame)

        assert (game_frames(loner), game_master.calls) == ([], [])

    async def test_a_call_alone_gets_its_question(self, link):
        [host] = await call_of(link, "host")

        await open_a_question([host], host)

        assert game_frames(host) == asked(host)


class TestReveal:
    async def test_the_starters_reveal_runs_on_the_starters_socket_with_the_quiz_so_far(self, link, game_master):
        call = await call_of(link, "host", "guest")
        game_master.answer_next(Line("Frage?"), Line(REVEAL_LINE))
        await open_a_question(call, call[1])

        await call[1].say(REVEAL)
        await sent_to_all(call, IDLE)

        [_, reveal] = game_master.calls
        assert reveal.starter is call[1].socket
        assert reveal.transcript == (
            Turn(Speaker.HOPIN, NEXT_QUESTION_REQUEST),
            Turn(Speaker.GAME_MASTER, "Frage?"),
            Turn(Speaker.HOPIN, REVEAL_REQUEST),
        )

    async def test_every_seat_sees_thinking_then_the_reveal_line_then_idle(self, link, game_master):
        call = await call_of(link, "host", "guest")
        game_master.answer_next(Line("Frage?"), Line(REVEAL_LINE))
        await open_a_question(call, call[1])

        await call[1].say(REVEAL)
        await sent_to_all(call, IDLE)

        for browser in call:
            assert game_frames(browser) == [*asked(call[1], "Frage?"), thinking(call[1]), line(REVEAL_LINE), IDLE]

    async def test_a_reveal_from_another_seat_is_refused_to_that_seat_alone(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")
        await open_a_question([host, guest], guest)

        await host.say(REVEAL)
        await sent(host, refused(GameRefusal.NOT_STARTER))

        assert game_frames(host) == [*asked(guest), refused(GameRefusal.NOT_STARTER)]
        assert game_frames(guest) == asked(guest)
        assert len(game_master.calls) == 1

    async def test_a_reveal_with_no_quiz_is_refused_to_its_sender(self, link, game_master):
        [host] = await call_of(link, "host")

        await host.say(REVEAL)
        await sent(host, refused(GameRefusal.NOT_STARTER))

        assert game_master.calls == []

    async def test_a_second_reveal_while_the_reveal_runs_is_ignored(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")
        await open_a_question([host, guest], guest)
        held = game_master.hold_next()
        await guest.say(REVEAL)
        await wait_within_cap(held.started)

        await guest.say(REVEAL)
        held.release.set()
        await sent_to_all([host, guest], IDLE)

        assert game_frames(guest) == [*asked(guest), thinking(guest), line(DEFAULT_QUESTION), IDLE]
        assert len(game_master.calls) == 2

    async def test_a_start_during_an_open_quiz_is_refused_to_its_sender_alone(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")
        await open_a_question([host, guest], guest)

        await host.say(START)
        await sent(host, refused(GameRefusal.QUIZ_RUNNING))

        assert game_frames(host) == [*asked(guest), refused(GameRefusal.QUIZ_RUNNING)]
        assert game_frames(guest) == asked(guest)
        assert len(game_master.calls) == 1

    async def test_a_refused_reveal_ends_the_quiz_with_the_reason_to_the_starter_and_idle_to_all(
        self, link, game_master
    ):
        host, guest = await call_of(link, "host", "guest")
        game_master.answer_next(Line(DEFAULT_QUESTION), Refused(GameRefusal.NO_ACCESS))
        await open_a_question([host, guest], guest)

        await guest.say(REVEAL)
        await sent_to_all([host, guest], IDLE)
        await open_a_question([host, guest], host)

        assert game_frames(guest)[:6] == [*asked(guest), thinking(guest), refused(GameRefusal.NO_ACCESS), IDLE]
        assert game_frames(host)[:5] == [*asked(guest), thinking(guest), IDLE]

    async def test_a_reveal_past_its_deadline_ends_the_quiz(self, link, game_master, scheduler):
        host, guest = await call_of(link, "host", "guest")
        await open_a_question([host, guest], guest)
        held = game_master.hold_next()
        await guest.say(REVEAL)
        await wait_within_cap(held.started)

        await scheduler.advance(GAME_RUN_DEADLINE_SECONDS)
        await sent_to_all([host, guest], IDLE)
        await open_a_question([host, guest], host)

        assert game_frames(guest)[:6] == [*asked(guest), thinking(guest), refused(GameRefusal.UNAVAILABLE), IDLE]
        assert game_master.calls[1].cancelled

    async def test_a_new_quiz_starts_with_an_empty_transcript(self, link, game_master):
        [host] = await call_of(link, "host")
        await open_a_question([host], host)
        await host.say(REVEAL)
        await sent(host, IDLE)

        await open_a_question([host], host)

        assert game_master.calls[-1].transcript == OPENING


class TestOneRunPerRoom:
    async def test_a_second_start_in_flight_is_refused_to_its_sender_only(self, link, game_master):
        host, guest, third = await call_of(link, "host", "guest", "third")
        held = await start_and_hold(game_master, guest)

        await third.say(START)
        await sent(third, refused(GameRefusal.ALREADY_ANSWERING))

        assert game_frames(third) == [thinking(guest), refused(GameRefusal.ALREADY_ANSWERING)]
        assert game_frames(guest) == game_frames(host) == [thinking(guest)]
        assert len(game_master.calls) == 1
        held.release.set()
        await sent_to_all([host, guest, third], answering(guest))

    async def test_two_rooms_run_at_once(self, link, game_master):
        [first] = await call_of(link, "first")
        [second] = await call_of(link, "second")
        held = await start_and_hold(game_master, first)

        await open_a_question([second], second)

        assert game_frames(second) == asked(second)
        held.release.set()
        await sent(first, answering(first))

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
        await open_a_question([host, guest], host)

        assert game_frames(guest) == [thinking(guest), refused(GameRefusal.UNAVAILABLE), IDLE, *asked(host)]
        assert_phases_in_order([host, guest], ["thinking", "idle", "thinking", "answering"])

    async def test_a_start_while_the_questions_delivery_is_held_at_a_seat_is_refused_and_answering_precedes_the_reveal(
        self, link, game_master
    ):
        host, guest, third = await call_of(link, "host", "guest", "third")
        held = await start_and_hold(game_master, guest)
        await sent(third, thinking(guest))
        gate = asyncio.Event()
        third.socket.send_gate = gate

        held.release.set()
        await sent(host, question())
        await host.say(START)
        await sent(host, refused(GameRefusal.ALREADY_ANSWERING))
        gate.set()
        await sent_to_all([host, guest, third], answering(guest))
        await guest.say(REVEAL)
        await sent_to_all([host, guest, third], IDLE)

        assert len(game_master.calls) == 2
        assert_phases_in_order([host, guest, third], ["thinking", "answering", "thinking", "idle"])

    async def test_a_reveal_while_answering_is_held_at_a_seat_runs_after_every_seat_has_answering(
        self, link, game_master
    ):
        host, guest, third = await call_of(link, "host", "guest", "third")
        gate = hold_writes_from(guest, answering(host))

        await host.say(START)
        await sent(host, answering(host))
        await host.say(REVEAL)
        gate.set()
        await sent_to_all([host, guest, third], IDLE)

        assert len(game_master.calls) == 2
        assert_phases_in_order([host, guest, third], ["thinking", "answering", "thinking", "idle"])


def hold_writes_from(browser, frame):
    """Hold the browser's writes from the frame on until the returned gate opens."""
    gate = asyncio.Event()
    send_text = browser.socket.send_text

    async def send_text_once_open(text):
        if without_seq(json.loads(text)) == frame:
            browser.socket.send_gate = gate
        await send_text(text)

    browser.socket.send_text = send_text_once_open
    return gate


def assert_phases_in_order(browsers, phases):
    for browser in browsers:
        sent_phases = [(seq, frame["phase"]) for seq, frame in sequenced_game_frames(browser) if "phase" in frame]
        assert [phase for _, phase in sent_phases] == phases
        assert [seq for seq, _ in sent_phases] == sorted(seq for seq, _ in sent_phases)


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
        assert game_frames(guest) == [thinking(guest), refused(GameRefusal.UNAVAILABLE), IDLE]
        assert game_frames(host) == [thinking(guest), IDLE]

    async def test_a_run_answered_in_time_leaves_no_deadline_behind(self, link, scheduler):
        [host] = await call_of(link, "host")

        await open_a_question([host], host)

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

    async def test_ending_the_call_while_the_question_is_held_at_a_seat_sends_no_game_frame_after_room_closed(
        self, link, game_master
    ):
        host, guest, third = await call_of(link, "host", "guest", "third")
        held = await start_and_hold(game_master, host)
        await sent(third, thinking(host))
        gate = asyncio.Event()
        guest.socket.send_gate = gate
        held.release.set()
        await sent(host, question())

        with structlog.testing.capture_logs() as logs:
            push_unsettled(host, END_ROOM)
            await host.socket.wait_until(lambda: host.frames(SignalType.ROOM_CLOSED))
            gate.set()
            await shut_down_within_cap(link.handler)

        assert_no_driver_failed(logs)
        assert_no_game_frame_after_room_closed([host, guest, third])
        assert question() in game_frames(guest)
        assert question() not in game_frames(third)

    async def test_ending_the_call_during_answering_sends_no_game_frame(self, link):
        host, guest = await call_of(link, "host", "guest")
        await open_a_question([host, guest], guest)

        await host.say(END_ROOM)
        await shut_down_within_cap(link.handler)

        assert game_frames(host) == game_frames(guest) == asked(guest)
        assert_no_game_frame_after_room_closed([host, guest])

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

    async def test_a_new_call_under_an_ended_calls_room_id_starts_a_new_quiz(self, link, game_master):
        [host] = await call_of(link, "host")
        await open_a_question([host], host)
        await host.say(END_ROOM)

        [again] = await call_of(link, "again")
        await open_a_question([again], again)

        assert again.socket.sent[0]["room_id"] == host.socket.sent[0]["room_id"]
        assert game_master.calls[-1].transcript == OPENING


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
        assert game_frames(host) == game_frames(guest) == [thinking(guest)]

    async def test_shutting_down_during_answering_ends_the_quiz_so_a_reveal_finds_none(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")
        await open_a_question([host, guest], guest)

        await shut_down_within_cap(link.handler)
        await guest.say(REVEAL)
        await sent(guest, refused(GameRefusal.NOT_STARTER))

        assert game_frames(host) == asked(guest)
        assert len(game_master.calls) == 1

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
    async def test_a_write_that_fails_at_one_seat_leaves_the_lines_and_phases_to_the_others_and_is_logged_by_its_type(
        self, link, game_master
    ):
        host, guest, third = await call_of(link, "host", "guest", "third")
        game_master.answer_next(Line(DEFAULT_QUESTION), Line(REVEAL_LINE))
        held = await start_and_hold(game_master, host)
        await sent(third, thinking(host))
        guest.socket.send_text = broken_write

        with structlog.testing.capture_logs() as logs:
            held.release.set()
            await sent_to_all([host, third], answering(host))
            await yield_until(lambda: driver_failed(logs))
        await host.say(REVEAL)
        await sent(host, IDLE)

        assert game_frames(host) == game_frames(third) == [*asked(host), thinking(host), line(REVEAL_LINE), IDLE]
        assert "a write that broke" not in repr(logs)

    async def test_a_write_that_fails_while_announcing_thinking_leaves_the_run_the_rooms_until_it_ends(
        self, link, game_master
    ):
        host, guest, third = await call_of(link, "host", "guest", "third")
        guest.socket.send_text = broken_write

        with structlog.testing.capture_logs() as logs:
            held = await start_and_hold(game_master, host)
            await sent(third, thinking(host))
            await third.say(START)
            await sent(third, refused(GameRefusal.ALREADY_ANSWERING))
            held.release.set()
            await sent_to_all([host, third], answering(host))
            await yield_until(lambda: driver_failed(logs))

        assert len(game_master.calls) == 1
        assert game_frames(host) == asked(host)
        assert game_frames(third) == [thinking(host), refused(GameRefusal.ALREADY_ANSWERING), *asked(host)[1:]]

    async def test_a_write_that_fails_while_announcing_thinking_leaves_the_run_to_shutdown(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")
        guest.socket.send_text = broken_write
        await start_and_hold(game_master, host)
        await sent(host, thinking(host))

        await shut_down_within_cap(link.handler)

        [call] = game_master.calls
        assert (call.cancelled, call.finished) == (True, True)


class TestSeatsComingAndGoing:
    async def test_the_starter_leaving_mid_run_leaves_the_question_to_the_rest(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")
        held = await start_and_hold(game_master, guest)

        await guest.drop(code=1000)
        held.release.set()
        await sent(host, answering(guest))

        assert game_frames(host) == asked(guest)

    async def test_a_refusal_for_a_starter_who_left_is_dropped(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")
        game_master.answer_next(Refused(GameRefusal.NO_ACCESS))
        held = await start_and_hold(game_master, guest)

        await guest.drop(code=1000)
        held.release.set()
        await sent(host, IDLE)

        assert game_frames(host) == [thinking(guest), IDLE]

    async def test_a_resume_mid_run_replays_the_question_and_answering(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")
        held = await start_and_hold(game_master, host)
        await sent(guest, thinking(host))

        await guest.drop()
        held.release.set()
        await sent(host, answering(host))
        resumed = await guest.resume()
        await resumed.wait_until(lambda: answering(host) in [without_seq(frame) for frame in resumed.sent])

        replayed = [without_seq(frame) for frame in resumed.sent if frame["type"] in GAME_TYPES]
        assert replayed == [question(), answering(host)]

    async def test_a_resume_while_thinking_ends_in_thinking(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")
        await guest.drop()
        held = await start_and_hold(game_master, host)
        await sent(host, thinking(host))

        resumed = await guest.resume()

        assert [without_seq(frame) for frame in resumed.sent if frame["type"] in GAME_TYPES] == [thinking(host)]
        held.release.set()
        await sent(guest, answering(host))

    async def test_a_late_joiner_mid_run_sees_thinking_then_the_question(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")
        held = await start_and_hold(game_master, guest)

        late = await link.join("late", host.socket.sent[0]["room_id"])
        await sent(late, thinking(guest))
        held.release.set()
        await sent(late, answering(guest))

        assert game_frames(late) == asked(guest)

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
        await sent(guest, answering(host))

        assert game_frames(guest) == asked(host)

    async def test_a_joiner_during_answering_gets_answering_and_no_question(self, link):
        host, guest = await call_of(link, "host", "guest")
        await open_a_question([host, guest], host)

        late = await link.join("late", host.socket.sent[0]["room_id"])

        assert game_frames(late) == [answering(host)]

    async def test_a_joiner_after_the_quiz_sees_no_phase(self, link):
        host, guest = await call_of(link, "host", "guest")
        await open_a_question([host, guest], host)
        await host.say(REVEAL)
        await sent_to_all([host, guest], IDLE)

        late = await link.join("late", host.socket.sent[0]["room_id"])

        assert game_frames(late) == []


async def test_six_seats_get_the_question(scheduler, game):
    handler = SignalingHandler(
        RoomManager(max_participants_per_room=MAX_PARTICIPANTS_PER_ROOM),
        SocketAccountResolver(),
        scheduler,
        game,
    )
    link = Link(handler)
    call = await call_of(link, *[f"person{number}" for number in range(MAX_PARTICIPANTS_PER_ROOM)])

    await open_a_question(call, call[3])

    assert all(game_frames(browser) == asked(call[3]) for browser in call)
    await link.close_all()
    await shut_down_within_cap(handler)
