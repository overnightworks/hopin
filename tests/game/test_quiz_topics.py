from functools import partial

import pytest

from hopin.game import draw_quiz_topics
from hopin.game.quiz import QUIZ_ROUNDS, QUIZ_TOPICS, question_request
from hopin.game.table import Game, Line
from tests.game.support.table_world import END, IDLE, call_of, let_every_task_run, open_a_question, sent_to_all

FIRST_QUIZ = tuple(QUIZ_TOPICS[:QUIZ_ROUNDS])
SECOND_QUIZ = tuple(QUIZ_TOPICS[QUIZ_ROUNDS : 2 * QUIZ_ROUNDS])


def test_the_topic_list_offers_at_least_two_quizzes_of_topics():
    assert len(set(QUIZ_TOPICS)) >= 2 * QUIZ_ROUNDS


def test_a_draw_gives_one_listed_topic_per_round_and_none_twice():
    topics = draw_quiz_topics()

    assert len(set(topics)) == QUIZ_ROUNDS
    assert set(topics) <= set(QUIZ_TOPICS)


@pytest.fixture
def game(scheduler, game_master):
    drawn = iter([FIRST_QUIZ, SECOND_QUIZ])

    return partial(Game, scheduler=scheduler, game_master=game_master, topic_drawer=lambda: next(drawn))


@pytest.mark.asyncio
async def test_each_quiz_started_in_a_room_draws_its_own_topics(link, game_master):
    host, guest = await call_of(link, "host", "guest")
    game_master.answer_next(Line("Frage?"), Line("Frage?"))
    await open_a_question([host, guest], host)
    await host.say(END)
    await sent_to_all([host, guest], IDLE)
    await let_every_task_run()
    await open_a_question([host, guest], host)

    first, second = game_master.calls
    assert first.transcript[0].text == question_request(FIRST_QUIZ[0])
    assert second.transcript[0].text == question_request(SECOND_QUIZ[0])
