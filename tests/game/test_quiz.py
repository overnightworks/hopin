import pytest

from hopin.game.quiz import (
    FINAL_SCORE_REQUEST,
    NEXT_QUESTION_REQUEST,
    QUIZ_ROUNDS,
    REVEAL_REQUEST,
    Quiz,
    Speaker,
    Turn,
)
from hopin.signaling.frames import PeerId

STARTER = PeerId("starter")
OTHER = PeerId("other")


def test_a_new_quiz_asks_for_question_one():
    quiz = Quiz(STARTER)

    assert (quiz.round, quiz.transcript) == (1, (Turn(Speaker.HOPIN, NEXT_QUESTION_REQUEST),))


def test_the_reveal_request_follows_the_question_in_the_transcript():
    quiz = Quiz(STARTER).answered("Wie heißt die Hauptstadt von Portugal?").revealing()

    assert quiz.transcript == (
        Turn(Speaker.HOPIN, NEXT_QUESTION_REQUEST),
        Turn(Speaker.GAME_MASTER, "Wie heißt die Hauptstadt von Portugal?"),
        Turn(Speaker.HOPIN, REVEAL_REQUEST),
    )


def last_round_revealed():
    """The quiz as it waits for the final score."""
    quiz = Quiz(STARTER)
    for _ in range(QUIZ_ROUNDS):
        quiz = quiz.answered("Frage?").revealing().answered("Lissabon.")
    return quiz


@pytest.mark.parametrize(("peer_id", "may_reveal"), [(STARTER, True), (OTHER, False)], ids=["starter", "other"])
def test_only_the_starter_may_reveal_an_open_question(peer_id, may_reveal):
    quiz = Quiz(STARTER).answered("Frage?")

    assert quiz.may_reveal(peer_id) is may_reveal


@pytest.mark.parametrize(
    "quiz",
    [
        pytest.param(Quiz(STARTER), id="the question is still being asked"),
        pytest.param(Quiz(STARTER).answered("Frage?").revealing(), id="the answer is being revealed"),
        pytest.param(last_round_revealed(), id="the final score is being asked"),
        pytest.param(last_round_revealed().answered("Ihr wart toll."), id="the quiz is over"),
    ],
)
def test_no_reveal_while_no_question_is_open(quiz):
    assert not quiz.may_reveal(STARTER)


def test_a_revealed_answer_before_the_last_round_asks_for_the_next_question():
    quiz = Quiz(STARTER).answered("Frage?").revealing().answered("Lissabon.")

    assert (quiz.round, quiz.is_asking, quiz.transcript[-1]) == (2, True, Turn(Speaker.HOPIN, NEXT_QUESTION_REQUEST))


def test_the_last_rounds_revealed_answer_asks_for_the_final_score():
    quiz = last_round_revealed()

    assert (quiz.round, quiz.transcript[-1]) == (QUIZ_ROUNDS, Turn(Speaker.HOPIN, FINAL_SCORE_REQUEST))
    assert not quiz.is_over


def test_the_final_score_ends_the_quiz():
    assert last_round_revealed().answered("Ihr wart toll.").is_over


def test_every_step_but_answering_and_over_waits_for_the_game_master():
    asking = Quiz(STARTER)

    assert [
        asking.awaits_game_master,
        asking.answered("Frage?").awaits_game_master,
        asking.answered("Frage?").revealing().awaits_game_master,
        last_round_revealed().awaits_game_master,
        last_round_revealed().answered("Ihr wart toll.").awaits_game_master,
    ] == [True, False, True, True, False]
