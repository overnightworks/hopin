import pytest

from hopin.game.quiz import NEXT_QUESTION_REQUEST, REVEAL_REQUEST, Quiz, Speaker, Turn
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


@pytest.mark.parametrize(("peer_id", "may_reveal"), [(STARTER, True), (OTHER, False)], ids=["starter", "other"])
def test_only_the_starter_may_reveal_an_open_question(peer_id, may_reveal):
    quiz = Quiz(STARTER).answered("Frage?")

    assert quiz.may_reveal(peer_id) is may_reveal


@pytest.mark.parametrize(
    "quiz",
    [
        pytest.param(Quiz(STARTER), id="the question is still being asked"),
        pytest.param(Quiz(STARTER).answered("Frage?").revealing(), id="the answer is being revealed"),
        pytest.param(Quiz(STARTER).answered("Frage?").revealing().answered("Lissabon."), id="the quiz is over"),
    ],
)
def test_no_reveal_while_no_question_is_open(quiz):
    assert not quiz.may_reveal(STARTER)


def test_the_revealed_answer_ends_a_one_round_quiz():
    quiz = Quiz(STARTER).answered("Frage?").revealing().answered("Lissabon.")

    assert quiz.is_over
