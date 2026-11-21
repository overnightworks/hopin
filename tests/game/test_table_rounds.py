import pytest

from hopin.game.quiz import QUIZ_ROUNDS, Speaker, Turn, final_score_request
from hopin.game.table import Line
from tests.game.support.table_world import (
    IDLE,
    REVEAL,
    START,
    answer,
    answering,
    call_of,
    game_frames,
    line,
    question,
    scores_of,
    sent_count_to_all,
    sent_to_all,
    thinking,
)

pytestmark = pytest.mark.asyncio

FINAL_LINE = "Ihr wart alle großartig."
RUN_COUNT = 2 * QUIZ_ROUNDS + 1


def question_text(round_number):
    return f"Frage {round_number}?"


def answer_text(round_number):
    return f"Antwort {round_number}."


def scripted_quiz(game_master):
    """The game master's lines for a whole quiz, in the order of its runs."""
    lines = [
        Line(text) for number in range(1, QUIZ_ROUNDS + 1) for text in (question_text(number), answer_text(number))
    ]
    game_master.answer_next(*lines, Line(FINAL_LINE))


async def play_every_round(call, starter):
    """Start the quiz and reveal every question as soon as every seat has it, until the final line's idle."""
    await starter.say(START)
    for number in range(1, QUIZ_ROUNDS + 1):
        await sent_to_all(call, answering(starter, number))
        await starter.say(REVEAL)
    await sent_to_all(call, IDLE)


def frames_of_a_whole_quiz(starter):
    rounds = [
        [
            thinking(starter, number),
            question(question_text(number), number),
            answering(starter, number),
            thinking(starter, number),
            line(answer_text(number)),
        ]
        for number in range(1, QUIZ_ROUNDS + 1)
    ]
    return [frame for frames in rounds for frame in frames] + [
        thinking(starter, QUIZ_ROUNDS),
        line(FINAL_LINE, final=True),
        IDLE,
    ]


class TestFiveRounds:
    async def test_five_reveals_make_eleven_runs_on_the_starters_socket_ending_in_the_final_line(
        self, link, game_master
    ):
        """GM-05, GM-22: Five reveals make eleven runs on the starters socket ending in the final line."""
        call = await call_of(link, "host", "guest")
        scripted_quiz(game_master)

        await play_every_round(call, call[1])

        assert len(game_master.calls) == RUN_COUNT
        assert {run.starter for run in game_master.calls} == {call[1].socket}
        assert game_master.calls[-1].transcript[-1] == Turn(Speaker.HOPIN, final_score_request(()))

    async def test_every_seat_sees_the_rounds_in_order_the_final_line_and_idle_once_at_the_end(self, link, game_master):
        call = await call_of(link, "host", "guest")
        scripted_quiz(game_master)

        await play_every_round(call, call[1])

        for browser in call:
            assert game_frames(browser) == frames_of_a_whole_quiz(call[1])

    async def test_the_final_line_carries_the_score_counted_from_the_verdicts_for_every_seat(self, link, game_master):
        """GM-24: The final line carries the score counted from the verdicts for every seat."""
        host, guest = call = await call_of(link, "host", "guest")
        scripted_quiz(game_master)

        await guest.say(START)
        for number in range(1, QUIZ_ROUNDS + 1):
            await sent_to_all(call, answering(guest, number))
            await host.say(answer("Tejo"))
            await sent_count_to_all(call, 1, 2, answered_by=[host])
            await guest.say(REVEAL)
        await sent_to_all(call, IDLE)

        for browser in call:
            assert game_frames(browser)[-3:] == [
                thinking(guest, QUIZ_ROUNDS),
                line(FINAL_LINE, scores_of(("host", QUIZ_ROUNDS)), final=True),
                IDLE,
            ]
