import json

import pytest
import structlog.testing

from hopin.config.constants import ErrorReason, SignalType
from hopin.game.table import Line
from hopin.game.vocabulary import GameRefusal, GameSignal
from tests.game.support.table_world import (
    END_ROOM,
    REVEAL,
    START,
    answer,
    answer_count,
    answering,
    asked,
    assert_no_driver_failed,
    assert_no_game_frame_after_room_closed,
    call_of,
    game_frames,
    hold_writes_from,
    let_every_task_run,
    line,
    open_a_question,
    push_unsettled,
    question,
    refused,
    sent,
    sent_to_all,
    shut_down_within_cap,
    thinking,
    wait_within_cap,
    yield_until,
)
from tests.signaling.support.browsers import Browser
from tests.support.signaling_fakes import account_named, text_frame

pytestmark = pytest.mark.asyncio

PRIVATE_ANSWER = "Mondego-Geheimnis"
QUESTION_TWO = "Zweite?"


def script_two_questions(game_master):
    game_master.answer_next(Line("Frage?"), Line("Lissabon."), Line(QUESTION_TWO))


def revealed_into_question_two(starter):
    """What every seat sees after the reveal, whoever tapped it: the line, then question 2 with answering."""
    return [
        thinking(starter),
        line("Lissabon."),
        thinking(starter, 2),
        question(QUESTION_TWO, 2),
        answering(starter, 2),
    ]


async def test_an_answer_gives_every_seat_the_count_and_no_seat_its_text(link, game_master):
    host, guest = await call_of(link, "host", "guest")
    await open_a_question([host, guest], host)

    await guest.say(answer(PRIVATE_ANSWER))
    await sent_to_all([host, guest], answer_count(1, 2))

    for browser in (host, guest):
        assert PRIVATE_ANSWER not in json.dumps(browser.socket.sent)
        assert browser.frames(SignalType.CHAT) == []


async def test_a_second_answer_replaces_the_first_and_the_count_stays(link, game_master):
    host, guest = await call_of(link, "host", "guest")
    await open_a_question([host, guest], host)

    await guest.say(answer("Porto"))
    await guest.say(answer("Lissabon"))
    await sent(host, answer_count(1, 2), times=2)

    assert game_frames(host) == [*asked(host), answer_count(1, 2), answer_count(1, 2)]


async def test_the_last_present_players_answer_runs_the_reveal_on_the_starters_socket(link, game_master):
    host, guest = await call_of(link, "host", "guest")
    script_two_questions(game_master)
    await open_a_question([host, guest], guest)

    await host.say(answer("Porto"))
    await guest.say(answer("Lissabon"))
    await sent_to_all([host, guest], answering(guest, 2))

    for browser in (host, guest):
        assert game_frames(browser) == [
            *asked(guest, "Frage?"),
            answer_count(1, 2),
            answer_count(2, 2),
            *revealed_into_question_two(guest),
        ]
    assert game_master.calls[1].starter is guest.socket


async def test_the_starters_reveal_before_everyone_answered_runs_once_and_a_late_answer_is_refused_to_its_sender(
    link, game_master
):
    host, guest = await call_of(link, "host", "guest")
    script_two_questions(game_master)
    await open_a_question([host, guest], guest)
    held = game_master.hold_next()
    await guest.say(REVEAL)
    await wait_within_cap(held.started)

    await host.say(answer("Porto"))
    held.release.set()
    await sent_to_all([host, guest], answering(guest, 2))

    assert host.frames(GameSignal.GAME_REFUSED) == [
        {**refused(GameRefusal.ANSWER_CLOSED), "seq": host.frames(GameSignal.GAME_REFUSED)[0]["seq"]}
    ]
    assert guest.frames(GameSignal.GAME_REFUSED) == []
    assert game_frames(guest) == [*asked(guest, "Frage?"), *revealed_into_question_two(guest)]
    assert len(game_master.calls) == 3


async def test_a_parked_player_holds_the_reveal_back_until_it_answers_after_its_resume(link, game_master):
    host, guest = await call_of(link, "host", "guest")
    script_two_questions(game_master)
    await open_a_question([host, guest], host)
    await guest.drop(code=1006)

    await host.say(answer("Porto"))
    resumed = await guest.resume()
    await resumed.wait_until(lambda: answering(host) in game_frames(guest))
    await guest.say(answer("Lissabon"))
    await sent(host, answering(host, 2))

    assert game_frames(host) == [
        *asked(host, "Frage?"),
        answer_count(1, 2),
        answer_count(2, 2),
        *revealed_into_question_two(host),
    ]


async def test_a_seat_that_joined_during_answering_plays_from_the_next_question(link, game_master):
    host, guest = await call_of(link, "host", "guest")
    script_two_questions(game_master)
    await open_a_question([host, guest], host)
    late = await link.join("late", host.socket.sent[0]["room_id"])

    await late.say(answer("Tejo"))
    await host.say(answer("Porto"))
    await guest.say(answer("Lissabon"))
    await sent_to_all([host, guest, late], answering(host, 2))
    await late.say(answer("Tejo"))
    await sent(host, answer_count(1, 3))

    assert [frame["reason"] for frame in late.frames(GameSignal.GAME_REFUSED)] == [GameRefusal.ANSWER_CLOSED.value]
    counts = [frame for frame in game_frames(host) if frame["type"] == GameSignal.GAME_ANSWER_COUNT.value]
    assert counts == [answer_count(1, 2), answer_count(2, 2), answer_count(1, 3)]


async def test_a_seat_that_joins_while_the_question_line_is_held_is_told_answering_but_is_no_player(
    link, game_master, room_manager
):
    host, guest = await call_of(link, "host", "guest")
    room_id = host.socket.sent[0]["room_id"]
    gate = hold_writes_from(guest, question())
    await host.say(START)
    await sent(host, question())
    late = Browser(link, "late")
    late.sockets.append(await link.open(account_named("late")))
    late.socket.push(text_frame({"type": SignalType.JOIN.value, "room_id": room_id}))
    await yield_until(lambda: len(room_manager.get_room(room_id).participant_ids) == 3)

    gate.set()
    await sent_to_all([host, guest, late], answering(host))
    await late.say(answer("Porto"))

    assert [frame["reason"] for frame in late.frames(GameSignal.GAME_REFUSED)] == [GameRefusal.ANSWER_CLOSED.value]
    assert question() not in game_frames(late)


async def test_the_others_answers_start_no_run_when_the_starter_has_left(link, game_master):
    host, guest, third = await call_of(link, "host", "guest", "third")
    await open_a_question([host, guest, third], host)
    await host.drop(code=1000)

    await guest.say(answer("Porto"))
    await third.say(answer("Lissabon"))
    await let_every_task_run()

    assert game_frames(third)[-2:] == [answer_count(1, 2), answer_count(2, 2)]
    assert len(game_master.calls) == 1


async def test_an_answer_without_a_quiz_is_refused_to_its_sender_alone(link, game_master):
    host, guest = await call_of(link, "host", "guest")

    await host.say(answer("Porto"))

    assert [frame["reason"] for frame in host.frames(GameSignal.GAME_REFUSED)] == [GameRefusal.ANSWER_CLOSED.value]
    assert game_frames(guest) == []


@pytest.mark.parametrize(
    "frame",
    [
        pytest.param({"type": GameSignal.GAME_ANSWER.value, "text": 5}, id="not a string"),
        pytest.param({"type": GameSignal.GAME_ANSWER.value}, id="no text"),
        pytest.param(answer(" \n "), id="blank"),
    ],
)
async def test_an_answer_that_is_no_text_gets_the_invalid_message_error_and_counts_nothing(link, game_master, frame):
    host, guest = await call_of(link, "host", "guest")
    await open_a_question([host, guest], host)

    await guest.say(frame)

    [error] = guest.frames(SignalType.ERROR)
    assert error["reason"] == ErrorReason.INVALID_MESSAGE.value
    assert game_frames(host) == game_frames(guest) == asked(host)


async def test_a_newer_answer_wins_the_counts_a_held_seat_still_waits_for(link, game_master):
    host, guest, third = await call_of(link, "host", "guest", "third")
    await open_a_question([host, guest, third], host)
    gate = hold_writes_from(guest, answer_count(1, 3))

    push_unsettled(third, answer("Porto"))
    await sent(host, answer_count(1, 3))
    push_unsettled(host, answer("Lissabon"))
    await sent(host, answer_count(2, 3))
    gate.set()
    await sent_to_all([guest, third], answer_count(2, 3))

    assert answer_count(1, 3) not in game_frames(third)
    assert [game_frames(browser)[-1] for browser in (host, guest, third)] == [answer_count(2, 3)] * 3


async def test_ending_the_call_while_the_counts_are_sent_sends_no_game_frame_after_room_closed(link, game_master):
    host, guest, third = await call_of(link, "host", "guest", "third")
    await open_a_question([host, guest, third], host)
    gate = hold_writes_from(guest, answer_count(1, 3))

    with structlog.testing.capture_logs() as logs:
        push_unsettled(third, answer("Porto"))
        await sent(host, answer_count(1, 3))
        push_unsettled(host, END_ROOM)
        await host.socket.wait_until(lambda: host.frames(SignalType.ROOM_CLOSED))
        gate.set()
        for browser in (guest, third):
            await browser.socket.wait_until(lambda browser=browser: browser.frames(SignalType.ROOM_CLOSED))
        await shut_down_within_cap(link.handler)

    assert_no_driver_failed(logs)
    assert_no_game_frame_after_room_closed([host, guest, third])


async def test_no_log_event_carries_an_answer(link, game_master):
    host, guest = await call_of(link, "host", "guest")
    script_two_questions(game_master)
    await open_a_question([host, guest], host)

    with structlog.testing.capture_logs() as logs:
        await guest.say(answer(PRIVATE_ANSWER))
        await host.say(answer(PRIVATE_ANSWER))
        await sent_to_all([host, guest], answering(host, 2))

    assert PRIVATE_ANSWER not in json.dumps(logs, default=str)


async def test_the_reveal_run_gets_no_answer_in_its_transcript(link, game_master):
    host, guest = await call_of(link, "host", "guest")
    script_two_questions(game_master)
    await open_a_question([host, guest], host)

    await guest.say(answer(PRIVATE_ANSWER))
    await host.say(answer(PRIVATE_ANSWER))
    await sent_to_all([host, guest], answering(host, 2))

    assert all(PRIVATE_ANSWER not in turn.text for call in game_master.calls for turn in call.transcript)
