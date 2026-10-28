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
LATE = PeerId("late")
PLAYERS = frozenset({STARTER, OTHER})


def test_a_new_quiz_asks_for_question_one():
    quiz = Quiz(STARTER)

    assert (quiz.round, quiz.transcript) == (1, (Turn(Speaker.HOPIN, NEXT_QUESTION_REQUEST),))


def test_the_reveal_request_follows_the_question_in_the_transcript():
    quiz = Quiz(STARTER).heard("Wie heißt die Hauptstadt von Portugal?").revealing()

    assert quiz.transcript == (
        Turn(Speaker.HOPIN, NEXT_QUESTION_REQUEST),
        Turn(Speaker.GAME_MASTER, "Wie heißt die Hauptstadt von Portugal?"),
        Turn(Speaker.HOPIN, REVEAL_REQUEST),
    )


def last_round_revealed():
    """The quiz as it waits for the final score."""
    quiz = Quiz(STARTER)
    for _ in range(QUIZ_ROUNDS):
        quiz = quiz.heard("Frage?").revealing().heard("Lissabon.")
    return quiz


@pytest.mark.parametrize(("peer_id", "may_reveal"), [(STARTER, True), (OTHER, False)], ids=["starter", "other"])
def test_only_the_starter_may_reveal_an_open_question(peer_id, may_reveal):
    quiz = Quiz(STARTER).heard("Frage?")

    assert quiz.may_reveal(peer_id) is may_reveal


@pytest.mark.parametrize(
    "quiz",
    [
        pytest.param(Quiz(STARTER), id="the question is still being asked"),
        pytest.param(Quiz(STARTER).heard("Frage?").revealing(), id="the answer is being revealed"),
        pytest.param(last_round_revealed(), id="the final score is being asked"),
        pytest.param(last_round_revealed().heard("Ihr wart toll."), id="the quiz is over"),
    ],
)
def test_no_reveal_while_no_question_is_open(quiz):
    assert not quiz.may_reveal(STARTER)


def test_a_revealed_answer_before_the_last_round_asks_for_the_next_question():
    quiz = Quiz(STARTER).heard("Frage?").revealing().heard("Lissabon.")

    assert (quiz.round, quiz.is_asking, quiz.transcript[-1]) == (2, True, Turn(Speaker.HOPIN, NEXT_QUESTION_REQUEST))


def test_the_last_rounds_revealed_answer_asks_for_the_final_score():
    quiz = last_round_revealed()

    assert (quiz.round, quiz.transcript[-1]) == (QUIZ_ROUNDS, Turn(Speaker.HOPIN, FINAL_SCORE_REQUEST))
    assert not quiz.is_over


def test_the_final_score_ends_the_quiz():
    assert last_round_revealed().heard("Ihr wart toll.").is_over


def test_every_step_but_answering_and_over_waits_for_the_game_master():
    asking = Quiz(STARTER)

    assert [
        asking.awaits_game_master,
        asking.heard("Frage?").awaits_game_master,
        asking.heard("Frage?").revealing().awaits_game_master,
        last_round_revealed().awaits_game_master,
        last_round_revealed().heard("Ihr wart toll.").awaits_game_master,
    ] == [True, False, True, True, False]


def open_question():
    return Quiz(STARTER).heard("Frage?", PLAYERS)


@pytest.mark.parametrize(
    ("peer_id", "may_answer"),
    [(STARTER, True), (OTHER, True), (LATE, False)],
    ids=["starter", "other player", "not a player"],
)
def test_only_a_player_may_answer_an_open_question(peer_id, may_answer):
    assert open_question().may_answer(peer_id) is may_answer


@pytest.mark.parametrize(
    "quiz",
    [
        pytest.param(Quiz(STARTER), id="the question is still being asked"),
        pytest.param(open_question().revealing(), id="the answer is being revealed"),
        pytest.param(last_round_revealed(), id="the final score is being asked"),
        pytest.param(last_round_revealed().heard("Ihr wart toll."), id="the quiz is over"),
    ],
)
def test_nobody_may_answer_while_no_question_is_open(quiz):
    assert not quiz.may_answer(STARTER)


def test_a_second_answer_replaces_the_first_so_the_count_stays():
    quiz = open_question().with_answer(OTHER, "Porto").with_answer(OTHER, "Lissabon")

    assert quiz.answered_count(PLAYERS) == 1


def test_the_count_covers_only_players_who_are_present():
    quiz = open_question().with_answer(STARTER, "A").with_answer(OTHER, "B")

    assert (quiz.answered_count(PLAYERS), quiz.answered_count(frozenset({STARTER, LATE}))) == (2, 1)


@pytest.mark.parametrize(
    ("answering", "present", "everyone_answered"),
    [
        pytest.param((STARTER, OTHER), PLAYERS, True, id="all players answered"),
        pytest.param((STARTER,), PLAYERS, False, id="a present player has not answered"),
        pytest.param((STARTER, OTHER), frozenset({STARTER, LATE}), True, id="a player who left is not waited for"),
        pytest.param((STARTER,), frozenset({STARTER}), True, id="the one present player answered"),
        pytest.param((), frozenset({LATE}), False, id="no player is present"),
    ],
)
def test_everyone_answered_judges_the_present_players(answering, present, everyone_answered):
    quiz = open_question()
    for peer_id in answering:
        quiz = quiz.with_answer(peer_id, "A")

    assert quiz.everyone_answered(present) is everyone_answered


def test_the_next_round_starts_with_no_players_and_no_answers():
    quiz = open_question().with_answer(STARTER, "A").revealing().heard("Lissabon.")

    assert (quiz.players, quiz.answers, quiz.may_answer(STARTER)) == (frozenset(), (), False)


def test_revealing_keeps_the_answers_for_the_reveal_run():
    quiz = open_question().with_answer(STARTER, "A").revealing()

    assert quiz.answered_count(PLAYERS) == 1


def test_the_transcript_holds_no_answer_after_revealing():
    quiz = open_question().with_answer(STARTER, "Geheim").revealing()

    assert all("Geheim" not in turn.text for turn in quiz.transcript)
