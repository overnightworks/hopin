import asyncio

import pytest
import structlog.testing

from hopin.config.constants import SignalType
from hopin.game.vocabulary import GAME_RUN_DEADLINE_SECONDS, GameRefusal
from tests.game.support.table_world import (
    END,
    END_ROOM,
    ENDED,
    IDLE,
    OPENING,
    REVEAL,
    START,
    answering,
    asked,
    assert_no_driver_failed,
    assert_no_game_frame_after_room_closed,
    broken_write,
    call_of,
    game_frames,
    hold_writes_from,
    let_every_task_run,
    open_a_question,
    push_unsettled,
    refused,
    sent,
    sent_to_all,
    shut_down_within_cap,
    start_and_hold,
    thinking,
    wait_within_cap,
    yield_until,
)

pytestmark = pytest.mark.asyncio


class TestWhoMayEnd:
    async def test_the_starters_end_gives_every_seat_ended_then_idle_and_the_next_quiz_starts_afresh(
        self, link, game_master
    ):
        host, guest = await call_of(link, "host", "guest")
        await open_a_question([host, guest], guest)

        await guest.say(END)
        await sent_to_all([host, guest], IDLE)
        await let_every_task_run()
        await open_a_question([host, guest], host)

        for browser in (host, guest):
            assert game_frames(browser)[:5] == [*asked(guest), ENDED, IDLE]
        assert game_master.calls[-1].transcript == OPENING

    async def test_the_hosts_end_of_a_guests_quiz_gives_every_seat_ended_then_idle(self, link, game_master):
        host, guest, third = await call_of(link, "host", "guest", "third")
        await open_a_question([host, guest, third], guest)

        await host.say(END)
        await sent_to_all([host, guest, third], IDLE)
        await let_every_task_run()

        for browser in (host, guest, third):
            assert game_frames(browser) == [*asked(guest), ENDED, IDLE]
        assert len(game_master.calls) == 1

    async def test_a_third_seats_end_is_refused_to_that_seat_alone_and_the_quiz_goes_on(self, link, game_master):
        host, guest, third = await call_of(link, "host", "guest", "third")
        await open_a_question([host, guest, third], guest)

        await third.say(END)
        await sent(third, refused(GameRefusal.NOT_STARTER_OR_HOST))
        await guest.say(REVEAL)
        await sent_to_all([host, guest, third], answering(guest, 2))

        assert game_frames(third)[:4] == [*asked(guest), refused(GameRefusal.NOT_STARTER_OR_HOST)]
        for browser in (host, guest):
            assert refused(GameRefusal.NOT_STARTER_OR_HOST) not in game_frames(browser)
            assert ENDED not in game_frames(browser)

    async def test_an_end_with_no_quiz_sends_nothing_and_asks_nothing(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")

        await guest.say(END)
        await let_every_task_run()

        assert (game_frames(host), game_frames(guest), game_master.calls) == ([], [], [])


class TestEndWhileARunIsInFlight:
    async def test_the_game_masters_call_is_cancelled_and_the_starter_gets_no_unavailable(
        self, link, game_master, scheduler
    ):
        host, guest = await call_of(link, "host", "guest")
        await start_and_hold(game_master, guest)
        await sent_to_all([host, guest], thinking(guest))
        [call] = game_master.calls

        await guest.say(END)
        await sent_to_all([host, guest], IDLE)
        await yield_until(lambda: call.finished)
        await scheduler.advance(GAME_RUN_DEADLINE_SECONDS)
        await let_every_task_run()

        assert call.cancelled
        for browser in (host, guest):
            assert game_frames(browser) == [thinking(guest), ENDED, IDLE]

    async def test_a_reveal_waiting_behind_the_questions_delivery_never_asks_and_each_seat_is_told_once(
        self, link, game_master
    ):
        host, guest, third = await call_of(link, "host", "guest", "third")
        gate = hold_writes_from(guest, answering(host))
        await host.say(START)
        await sent(host, answering(host))
        await host.say(REVEAL)

        push_unsettled(host, END)
        await sent(host, ENDED)
        gate.set()
        await sent_to_all([guest, third], IDLE)
        await let_every_task_run()

        assert len(game_master.calls) == 1
        for browser in (host, guest, third):
            frames = game_frames(browser)
            assert (frames.count(ENDED), frames[-2:]) == (1, [ENDED, IDLE])

    async def test_a_start_while_the_cancelled_calls_cleanup_is_held_is_refused_and_the_next_quiz_asks_after_it(
        self, link, game_master
    ):
        host, guest = await call_of(link, "host", "guest")
        held = await start_and_hold(game_master, guest, hold_cleanup=True)
        await sent_to_all([host, guest], thinking(guest))
        await guest.say(END)
        await sent_to_all([host, guest], IDLE)
        await wait_within_cap(held.cleanup_started)

        await host.say(START)
        await sent(host, refused(GameRefusal.ALREADY_ANSWERING))
        held.cleanup_release.set()
        await let_every_task_run()
        await open_a_question([host, guest], host)

        first, second = game_master.calls
        assert first.finished
        assert second.transcript == OPENING
        assert game_frames(host) == [thinking(guest), ENDED, IDLE, refused(GameRefusal.ALREADY_ANSWERING), *asked(host)]


class TestEndAgainstTheRoomsEnd:
    async def test_the_rooms_end_while_ended_is_held_at_a_seat_sends_no_game_frame_after_room_closed(
        self, link, game_master
    ):
        host, guest, starter = await call_of(link, "host", "guest", "starter")
        await open_a_question([host, guest, starter], starter)
        gate = asyncio.Event()
        guest.socket.send_gate = gate
        with structlog.testing.capture_logs() as logs:
            push_unsettled(starter, END)
            await sent(host, ENDED)
            push_unsettled(host, END_ROOM)
            await host.socket.wait_until(lambda: host.frames(SignalType.ROOM_CLOSED))
            gate.set()
            for browser in (guest, starter):
                await browser.socket.wait_until(lambda browser=browser: browser.frames(SignalType.ROOM_CLOSED))
            await shut_down_within_cap(link.handler)

        assert_no_driver_failed(logs)
        assert_no_game_frame_after_room_closed([host, guest, starter])
        assert ENDED not in game_frames(starter)


class TestEndWithABrokenWrite:
    async def test_a_write_that_fails_at_one_seat_leaves_ended_and_idle_to_the_others_and_is_logged_by_its_type(
        self, link
    ):
        host, guest, third = await call_of(link, "host", "guest", "third")
        await open_a_question([host, guest, third], host)
        guest.socket.send_text = broken_write

        with structlog.testing.capture_logs() as logs:
            await host.say(END)
            await sent_to_all([host, third], IDLE)

        for browser in (host, third):
            assert game_frames(browser) == [*asked(host), ENDED, IDLE]
        assert [log["error"] for log in logs if log["event"] == "game_end_failed"] == ["RuntimeError"] * 2
        assert "a write that broke" not in repr(logs)
