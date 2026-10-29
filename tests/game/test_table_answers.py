import json

import pytest
import structlog.testing

from hopin.config.constants import ErrorReason, SignalType
from hopin.game.vocabulary import GAME_ANSWER_MAX_CHARACTERS, GameRefusal, GameSignal
from tests.game.support.table_world import (
    REVEAL,
    START,
    answer,
    answer_count,
    answering,
    asked,
    broken_write,
    call_of,
    game_frames,
    hold_writes_from,
    let_every_task_run,
    open_a_question,
    push_unsettled,
    question,
    refused,
    revealed_answers,
    revealed_into_question_two,
    script_two_questions,
    sent,
    sent_to_all,
    wait_within_cap,
    yield_until,
)
from tests.signaling.support.browsers import Browser
from tests.support.signaling_fakes import account_named, text_frame

pytestmark = pytest.mark.asyncio

PRIVATE_ANSWER = "Mondego-Geheimnis"


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
    assert revealed_answers(game_master) == [
        {"name": "host", "antwort": "Porto"},
        {"name": "guest", "antwort": "Lissabon"},
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


async def test_the_last_answer_reveals_once_the_question_run_still_held_by_a_departed_seat_ends(link, game_master):
    host, guest, third = await call_of(link, "host", "guest", "third")
    script_two_questions(game_master)
    gate = hold_writes_from(third, answering(host))
    await host.say(START)
    await sent_to_all([host, guest], answering(host))
    await third.drop(code=1000)

    await host.say(answer("Porto"))
    await guest.say(answer("Lissabon"))
    await sent(guest, answer_count(2, 2))
    gate.set()
    await sent_to_all([host, guest], answering(host, 2))

    assert game_frames(host) == [
        *asked(host, "Frage?"),
        answer_count(1, 2),
        answer_count(2, 2),
        *revealed_into_question_two(host),
    ]
    assert len(game_master.calls) == 3


async def test_the_starters_reveal_while_the_last_counts_are_sent_runs_one_reveal(link, game_master):
    host, guest, third = await call_of(link, "host", "guest", "third")
    script_two_questions(game_master)
    await open_a_question([host, guest, third], host)
    gate = hold_writes_from(guest, answer_count(3, 3))
    await host.say(answer("Porto"))
    await guest.say(answer("Lissabon"))

    push_unsettled(third, answer("Faro"))
    await sent(host, answer_count(3, 3))
    await host.say(REVEAL)
    gate.set()
    await sent_to_all([host, guest, third], answering(host, 2))
    await let_every_task_run()

    assert len(game_master.calls) == 3


async def test_a_seat_whose_write_breaks_does_not_stop_the_counts_the_reveal_or_the_sender(link, game_master):
    host, guest = await call_of(link, "host", "guest")
    script_two_questions(game_master)
    await open_a_question([host, guest], host)
    guest.socket.send_text = broken_write

    await guest.say(answer("Porto"))
    await host.say(answer("Lissabon"))
    await sent(host, answering(host, 2))

    assert game_frames(host) == [
        *asked(host, "Frage?"),
        answer_count(1, 2),
        answer_count(2, 2),
        *revealed_into_question_two(host),
    ]
    assert host.socket.closed_with is None


async def test_the_automatic_reveal_run_gets_each_players_newest_answer_with_the_name(link, game_master):
    host, guest = await call_of(link, "host", "guest")
    script_two_questions(game_master)
    await open_a_question([host, guest], host)

    await guest.say(answer("Porto"))
    await guest.say(answer(PRIVATE_ANSWER))
    await host.say(answer("Lissabon"))
    await sent_to_all([host, guest], answering(host, 2))

    assert revealed_answers(game_master) == [
        {"name": "guest", "antwort": PRIVATE_ANSWER},
        {"name": "host", "antwort": "Lissabon"},
    ]


async def test_the_starters_reveal_before_everyone_answered_lists_only_those_who_answered(link, game_master):
    host, guest, third = await call_of(link, "host", "guest", "third")
    script_two_questions(game_master)
    await open_a_question([host, guest, third], host)

    await guest.say(answer("Porto"))
    await host.say(REVEAL)
    await sent_to_all([host, guest, third], answering(host, 2))

    assert revealed_answers(game_master) == [{"name": "guest", "antwort": "Porto"}]


async def test_the_reveal_run_gets_the_newest_answer_when_two_players_answers_race(link, game_master):
    host, guest, third = await call_of(link, "host", "guest", "third")
    script_two_questions(game_master)
    await open_a_question([host, guest, third], host)
    gate = hold_writes_from(guest, answer_count(3, 3))
    await host.say(answer("Porto"))
    await guest.say(answer("Lissabon"))

    push_unsettled(third, answer("Faro"))
    await sent(host, answer_count(3, 3))
    push_unsettled(host, answer("Tejo"))
    await sent(host, answer_count(3, 3), times=2)
    gate.set()
    await sent_to_all([host, guest, third], answering(host, 2))
    await let_every_task_run()

    assert len(game_master.calls) == 3
    assert {"name": "host", "antwort": "Tejo"} in revealed_answers(game_master)
    assert {"name": "host", "antwort": "Porto"} not in revealed_answers(game_master)


async def test_an_answer_over_the_cap_is_refused_to_its_sender_alone_and_counts_nothing(link, game_master):
    host, guest = await call_of(link, "host", "guest")
    await open_a_question([host, guest], host)

    await guest.say(answer("x" * (GAME_ANSWER_MAX_CHARACTERS + 1)))
    await sent(guest, refused(GameRefusal.ANSWER_TOO_LONG))

    assert game_frames(guest) == [*asked(host), refused(GameRefusal.ANSWER_TOO_LONG)]
    assert game_frames(host) == asked(host)
    await host.say(answer("Porto"))
    await sent_to_all([host, guest], answer_count(1, 2))


async def test_an_answer_of_exactly_the_cap_with_spaces_around_it_is_accepted(link, game_master):
    host, guest = await call_of(link, "host", "guest")
    await open_a_question([host, guest], host)

    await guest.say(answer(f"  {'x' * GAME_ANSWER_MAX_CHARACTERS}  "))

    await sent_to_all([host, guest], answer_count(1, 2))


async def test_an_over_long_overwrite_keeps_the_earlier_answer_for_the_reveal(link, game_master):
    host, guest = await call_of(link, "host", "guest")
    script_two_questions(game_master)
    await open_a_question([host, guest], host)

    await guest.say(answer("Porto"))
    await guest.say(answer("x" * (GAME_ANSWER_MAX_CHARACTERS + 1)))
    await sent(guest, refused(GameRefusal.ANSWER_TOO_LONG))
    await host.say(answer("Lissabon"))
    await sent_to_all([host, guest], answering(host, 2))

    assert {"name": "guest", "antwort": "Porto"} in revealed_answers(game_master)


async def test_a_closed_question_refuses_an_over_long_answer_as_closed(link, game_master):
    _, guest = await call_of(link, "host", "guest")

    await guest.say(answer("x" * (GAME_ANSWER_MAX_CHARACTERS + 1)))

    assert [frame["reason"] for frame in guest.frames(GameSignal.GAME_REFUSED)] == [GameRefusal.ANSWER_CLOSED.value]


async def test_no_log_event_carries_an_answer_or_a_name(link, game_master):
    host, guest = await call_of(link, "host", "guest")
    script_two_questions(game_master)
    await open_a_question([host, guest], host)

    with structlog.testing.capture_logs() as logs:
        await guest.say(answer(PRIVATE_ANSWER))
        await host.say(answer(PRIVATE_ANSWER))
        await sent_to_all([host, guest], answering(host, 2))

    logged = json.dumps(logs, default=str)
    assert PRIVATE_ANSWER not in logged
    assert "guest" not in logged
