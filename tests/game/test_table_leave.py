import asyncio

import pytest
import structlog.testing

from hopin.config.constants import RESUME_WINDOW_SECONDS, SESSION_RECHECK_SECONDS
from hopin.game.table import Line, Refused
from hopin.game.vocabulary import GAME_RUN_DEADLINE_SECONDS, GameRefusal, GameSignal
from tests.game.support.fake_game_master import DEFAULT_QUESTION
from tests.game.support.table_world import (
    END,
    ENDED,
    IDLE,
    OPENING,
    REVEAL,
    START,
    answer,
    answer_count,
    answering,
    asked,
    call_of,
    ended_by,
    game_frames,
    hold_writes_from,
    line,
    open_a_question,
    push_unsettled,
    refused,
    revealed_answers,
    revealed_into_question_two,
    script_two_questions,
    sent,
    sent_to_all,
    start_and_hold,
    thinking,
    wait_within_cap,
    yield_until,
)
from tests.signaling.support.browsers import HeldClose

pytestmark = pytest.mark.asyncio


async def leaves(browser, _scheduler):
    await browser.drop(code=1000)


async def session_ends(browser, scheduler):
    browser.socket.account = None
    await scheduler.advance(SESSION_RECHECK_SECONDS)


def errors_in(logs):
    return [log for log in logs if log["log_level"] == "error"]


class TestTheStartersLeave:
    @pytest.mark.parametrize(
        "ending", [pytest.param(leaves, id="a leave"), pytest.param(session_ends, id="an ended session")]
    )
    async def test_during_answering_every_other_seat_sees_ended_with_the_name_then_idle_and_a_new_start_is_free(
        self, link, game_master, scheduler, ending
    ):
        host, guest, third = await call_of(link, "host", "guest", "third")
        await open_a_question([host, guest, third], guest)

        await ending(guest, scheduler)
        await sent_to_all([host, third], IDLE)
        await host.say(answer("Porto"))
        await host.say(START)
        await sent_to_all([host, third], answering(host))

        assert game_frames(host) == [
            *asked(guest),
            ended_by("guest"),
            IDLE,
            refused(GameRefusal.ANSWER_CLOSED),
            *asked(host),
        ]
        assert game_frames(third) == [*asked(guest), ended_by("guest"), IDLE, *asked(host)]
        assert game_master.calls[-1].transcript == OPENING

    async def test_a_dropped_connection_ends_the_quiz_only_once_its_grace_has_run_out(
        self, link, game_master, scheduler
    ):
        host, guest, third = await call_of(link, "host", "guest", "third")
        await open_a_question([host, guest, third], guest)

        await guest.drop()
        await scheduler.advance(RESUME_WINDOW_SECONDS - 1)
        within_the_grace = [game_frames(browser) for browser in (host, third)]
        await scheduler.advance(1)
        await sent_to_all([host, third], IDLE)

        assert within_the_grace == [asked(guest)] * 2
        for browser in (host, third):
            assert game_frames(browser) == [*asked(guest), ended_by("guest"), IDLE]

    async def test_a_return_within_the_grace_keeps_the_quiz_and_nobody_sees_it_end(self, link, game_master, scheduler):
        host, guest = await call_of(link, "host", "guest")
        await open_a_question([host, guest], guest)

        await guest.drop()
        await scheduler.advance(RESUME_WINDOW_SECONDS - 1)
        await guest.resume()
        await guest.say(REVEAL)
        await sent_to_all([host, guest], answering(guest, 2))

        for browser in (host, guest):
            assert browser.frames(GameSignal.GAME_ENDED) == []

    async def test_while_the_question_is_asked_the_call_is_cancelled_and_the_others_see_ended_then_idle(
        self, link, game_master, scheduler
    ):
        host, guest = await call_of(link, "host", "guest")
        await start_and_hold(game_master, guest)
        await sent(host, thinking(guest))
        [call] = game_master.calls

        await guest.drop(code=1000)
        await sent(host, IDLE)
        await yield_until(lambda: call.finished)
        await scheduler.advance(GAME_RUN_DEADLINE_SECONDS)

        assert call.cancelled
        assert game_frames(host) == [thinking(guest), ended_by("guest"), IDLE]

    async def test_while_the_reveal_is_asked_the_second_call_is_cancelled_and_the_others_see_ended_then_idle(
        self, link, game_master
    ):
        host, guest = await call_of(link, "host", "guest")
        await open_a_question([host, guest], guest)
        held = game_master.hold_next()
        await guest.say(REVEAL)
        await wait_within_cap(held.started)
        await sent(host, thinking(guest))

        await guest.drop(code=1000)
        await sent(host, IDLE)

        assert game_frames(host) == [*asked(guest), thinking(guest), ended_by("guest"), IDLE]
        [_question_call, reveal_call] = game_master.calls
        await yield_until(lambda: reveal_call.finished)
        assert reveal_call.cancelled

    async def test_a_chained_run_is_not_asked_for_a_starter_whose_seat_is_gone_and_the_notice_ends_the_quiz(
        self, link, game_master, scheduler
    ):
        host, guest = await call_of(link, "host", "guest")
        await open_a_question([host, guest], guest)
        held = game_master.hold_next()
        await guest.say(REVEAL)
        await wait_within_cap(held.started)
        await sent(host, thinking(guest))
        closing = HeldClose(guest.socket)
        guest.socket.account = None
        session_check = asyncio.create_task(scheduler.advance(SESSION_RECHECK_SECONDS))
        await closing.reached()

        with structlog.testing.capture_logs() as logs:
            held.release.set()
            await sent(host, line(DEFAULT_QUESTION))
            closing.release.set()
            await session_check
            await sent(host, IDLE)

        assert game_frames(host)[-3:] == [line(DEFAULT_QUESTION), ended_by("guest"), IDLE]
        assert len(game_master.calls) == 2
        assert [log for log in logs if log["event"] == "game_driver_failed"] == []

    async def test_an_ending_established_before_the_notice_wins_and_the_notice_sends_nothing(
        self, link, game_master, scheduler
    ):
        host, guest = await call_of(link, "host", "guest")
        game_master.answer_next(Line("Frage?"), Refused(GameRefusal.UNAVAILABLE))
        await open_a_question([host, guest], guest)
        held = game_master.hold_next()
        await guest.say(REVEAL)
        await wait_within_cap(held.started)
        await sent(host, thinking(guest))
        closing = HeldClose(guest.socket)
        guest.socket.account = None
        session_check = asyncio.create_task(scheduler.advance(SESSION_RECHECK_SECONDS))
        await closing.reached()

        with structlog.testing.capture_logs() as logs:
            held.release.set()
            await sent(host, IDLE)
            closing.release.set()
            await session_check

        [ended] = host.frames(GameSignal.GAME_ENDED)
        assert "leaver" not in ended
        assert game_frames(host)[-1] == IDLE
        assert errors_in(logs) == []

    async def test_a_leave_while_the_end_is_still_delivered_finds_the_quiz_over_and_sends_nothing(
        self, link, game_master, scheduler
    ):
        host, guest, third = await call_of(link, "host", "guest", "third")
        await open_a_question([host, guest, third], guest)
        closing = HeldClose(guest.socket)
        guest.socket.account = None
        session_check = asyncio.create_task(scheduler.advance(SESSION_RECHECK_SECONDS))
        await closing.reached()
        fourth = await link.join("fourth", host.socket.sent[0]["room_id"])
        entered = asyncio.Event()
        gate = hold_writes_from(fourth, ENDED, entered)

        with structlog.testing.capture_logs() as logs:
            push_unsettled(host, END)
            await wait_within_cap(entered)
            closing.release.set()
            await session_check
            gate.set()
            await sent_to_all([host, third, fourth], IDLE)

        for browser in (host, third, fourth):
            frames = game_frames(browser)
            assert (frames.count(ENDED), frames[-1]) == (1, IDLE)
            [ended] = browser.frames(GameSignal.GAME_ENDED)
            assert "leaver" not in ended
        assert errors_in(logs) == []


class TestAPlayersLeave:
    async def test_after_one_answer_every_seat_sees_the_count_shrink(self, link, game_master):
        host, guest, third = await call_of(link, "host", "guest", "third")
        await open_a_question([host, guest, third], host)
        await guest.say(answer("Porto"))
        await sent_to_all([host, guest, third], answer_count(1, 3))

        await third.drop(code=1000)
        await sent_to_all([host, guest], answer_count(1, 2))

        for browser in (host, guest):
            assert game_frames(browser) == [*asked(host), answer_count(1, 3), answer_count(1, 2)]

    async def test_a_count_still_on_its_way_never_overwrites_the_reduced_count(self, link, game_master):
        host, guest, third, fourth = await call_of(link, "host", "guest", "third", "fourth")
        await open_a_question([host, guest, third, fourth], host)
        entered = asyncio.Event()
        gate = hold_writes_from(guest, answer_count(1, 4), entered)
        try:
            push_unsettled(third, answer("Porto"))
            await wait_within_cap(entered)

            await guest.drop(code=1000)
            await sent_to_all([host, third, fourth], answer_count(1, 3))
        finally:
            gate.set()
        await link.settle(third.socket)

        for browser in (host, third, fourth):
            assert game_frames(browser)[-1] == answer_count(1, 3)
        for browser in (third, fourth):
            assert answer_count(1, 4) not in game_frames(browser)

    async def test_when_everyone_still_there_has_answered_the_reveal_follows_with_the_given_answers(
        self, link, game_master
    ):
        host, guest, third = await call_of(link, "host", "guest", "third")
        script_two_questions(game_master)
        await open_a_question([host, guest, third], host)
        await host.say(answer("Porto"))
        await guest.say(answer("Lissabon"))
        await sent_to_all([host, guest, third], answer_count(2, 3))

        await third.drop(code=1000)
        await sent_to_all([host, guest], answering(host, 2))

        for browser in (host, guest):
            assert game_frames(browser)[-7:] == [
                answer_count(2, 3),
                answer_count(2, 2),
                *revealed_into_question_two(host),
            ]
        assert {given["name"] for given in revealed_answers(game_master)} == {"host", "guest"}

    async def test_a_departed_players_given_answer_stays_in_the_reveal(self, link, game_master):
        host, guest, third = await call_of(link, "host", "guest", "third")
        script_two_questions(game_master)
        await open_a_question([host, guest, third], host)
        await guest.say(answer("Porto"))
        await sent_to_all([host, guest, third], answer_count(1, 3))
        await guest.drop(code=1000)
        await sent_to_all([host, third], answer_count(0, 2))

        await host.say(REVEAL)
        await sent_to_all([host, third], answering(host, 2))

        assert revealed_answers(game_master) == [{"name": "guest", "antwort": "Porto"}]

    async def test_a_seat_that_joined_during_answering_leaves_without_changing_the_count(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")
        await open_a_question([host, guest], host)
        await guest.say(answer("Porto"))
        await sent_to_all([host, guest], answer_count(1, 2))
        late = await link.join("late", host.socket.sent[0]["room_id"])

        await late.drop(code=1000)

        assert game_frames(host) == [*asked(host), answer_count(1, 2)]

    async def test_the_hosts_leave_does_not_end_a_guests_quiz_and_its_reveal_still_runs(self, link, game_master):
        host, guest = await call_of(link, "host", "guest")
        script_two_questions(game_master)
        await open_a_question([host, guest], guest)

        await host.drop(code=1000)
        await guest.say(REVEAL)
        await sent(guest, answering(guest, 2))

        assert game_frames(guest) == [*asked(guest, "Frage?"), *revealed_into_question_two(guest)]
