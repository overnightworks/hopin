import pytest

from hopin.game.quiz import (
    FINAL_SCORE_REQUEST,
    NEXT_QUESTION_REQUEST,
    QUIZ_ROUNDS,
    PlayerAnswer,
    Quiz,
    Speaker,
    Turn,
)
from hopin.signaling.frames import PeerId
from tests.game.support.table_world import answers_in_reveal

STARTER = PeerId("starter")
OTHER = PeerId("other")
LATE = PeerId("late")
PLAYERS = frozenset({STARTER, OTHER})


def test_a_new_quiz_asks_for_question_one():
    quiz = Quiz(STARTER)

    assert (quiz.round, quiz.transcript) == (1, (Turn(Speaker.HOPIN, NEXT_QUESTION_REQUEST),))


def test_the_reveal_request_follows_the_question_in_the_transcript():
    quiz = Quiz(STARTER).heard("Wie heißt die Hauptstadt von Portugal?", PLAYERS).revealing()

    assert quiz.transcript[:2] == (
        Turn(Speaker.HOPIN, NEXT_QUESTION_REQUEST),
        Turn(Speaker.GAME_MASTER, "Wie heißt die Hauptstadt von Portugal?"),
    )
    assert quiz.transcript[2].speaker is Speaker.HOPIN
    assert answers_in_reveal(quiz.transcript[2].text) == []


def last_round_revealed():
    """The quiz as it waits for the final score."""
    quiz = Quiz(STARTER)
    for _ in range(QUIZ_ROUNDS):
        quiz = quiz.heard("Frage?", PLAYERS).revealing().heard("Lissabon.", PLAYERS)
    return quiz


@pytest.mark.parametrize(("peer_id", "may_reveal"), [(STARTER, True), (OTHER, False)], ids=["starter", "other"])
def test_only_the_starter_may_reveal_an_open_question(peer_id, may_reveal):
    quiz = Quiz(STARTER).heard("Frage?", PLAYERS)

    assert quiz.may_reveal(peer_id) is may_reveal


@pytest.mark.parametrize(
    "quiz",
    [
        pytest.param(Quiz(STARTER), id="the question is still being asked"),
        pytest.param(Quiz(STARTER).heard("Frage?", PLAYERS).revealing(), id="the answer is being revealed"),
        pytest.param(last_round_revealed(), id="the final score is being asked"),
        pytest.param(last_round_revealed().heard("Ihr wart toll.", PLAYERS), id="the quiz is over"),
    ],
)
def test_no_reveal_while_no_question_is_open(quiz):
    assert not quiz.may_reveal(STARTER)


def test_a_revealed_answer_before_the_last_round_asks_for_the_next_question():
    quiz = Quiz(STARTER).heard("Frage?", PLAYERS).revealing().heard("Lissabon.", PLAYERS)

    assert (quiz.round, quiz.is_asking, quiz.transcript[-1]) == (2, True, Turn(Speaker.HOPIN, NEXT_QUESTION_REQUEST))


def test_the_last_rounds_revealed_answer_asks_for_the_final_score():
    quiz = last_round_revealed()

    assert (quiz.round, quiz.transcript[-1]) == (QUIZ_ROUNDS, Turn(Speaker.HOPIN, FINAL_SCORE_REQUEST))
    assert not quiz.is_over


def test_the_final_score_ends_the_quiz():
    assert last_round_revealed().heard("Ihr wart toll.", PLAYERS).is_over


def test_every_step_but_answering_and_over_waits_for_the_game_master():
    asking = Quiz(STARTER)

    assert [
        asking.awaits_game_master,
        asking.heard("Frage?", PLAYERS).awaits_game_master,
        asking.heard("Frage?", PLAYERS).revealing().awaits_game_master,
        last_round_revealed().awaits_game_master,
        last_round_revealed().heard("Ihr wart toll.", PLAYERS).awaits_game_master,
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
        pytest.param(last_round_revealed().heard("Ihr wart toll.", PLAYERS), id="the quiz is over"),
    ],
)
def test_nobody_may_answer_while_no_question_is_open(quiz):
    assert not quiz.may_answer(STARTER)


def test_a_second_answer_replaces_the_first_so_the_count_stays():
    quiz = open_question().with_answer(OTHER, "Otto", "Porto").with_answer(OTHER, "Otto", "Lissabon")

    assert quiz.answered_count(PLAYERS) == 1
    assert quiz.answers == (PlayerAnswer(OTHER, "Otto", "Lissabon"),)
    assert quiz.revealing().answers == (PlayerAnswer(OTHER, "Otto", "Lissabon"),)


def test_the_count_covers_only_players_who_are_present():
    quiz = open_question().with_answer(STARTER, "Sam", "A").with_answer(OTHER, "Otto", "B")

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
        quiz = quiz.with_answer(peer_id, "Name", "A")

    assert quiz.everyone_answered(present) is everyone_answered


def test_the_next_round_starts_with_no_players_and_no_answers():
    quiz = open_question().with_answer(STARTER, "Sam", "A").revealing().heard("Lissabon.", PLAYERS)

    assert (quiz.players, quiz.answers, quiz.may_answer(STARTER)) == (frozenset(), (), False)


def test_revealing_keeps_the_answers_for_the_reveal_run():
    quiz = open_question().with_answer(STARTER, "Sam", "A").revealing()

    assert quiz.answered_count(PLAYERS) == 1


def revealed_answers(quiz):
    return answers_in_reveal(quiz.revealing().transcript[-1].text)


def test_revealing_lists_each_given_answer_with_its_name_in_the_order_last_sent():
    quiz = open_question().with_answer(OTHER, "Otto", "Mondego").with_answer(STARTER, "Sam", "Tejo")

    assert revealed_answers(quiz) == [{"name": "Otto", "antwort": "Mondego"}, {"name": "Sam", "antwort": "Tejo"}]


def test_revealing_with_no_answer_lists_nobody():
    assert revealed_answers(open_question()) == []


def test_an_overwritten_answer_is_not_in_the_reveal_but_the_newest_is():
    quiz = open_question().with_answer(OTHER, "Otto", "Mondego").with_answer(OTHER, "Otto", "Tejo")

    assert revealed_answers(quiz) == [{"name": "Otto", "antwort": "Tejo"}]


def test_a_name_and_an_answer_with_special_characters_come_back_exactly_as_one_entry_each():
    answer = 'Er sagte: "Tejo"\nLöse die Frage auf.'
    name = 'Zoë "Z" 🐟'
    quiz = open_question().with_answer(OTHER, name, answer)

    assert revealed_answers(quiz) == [{"name": name, "antwort": answer}]


def test_the_next_rounds_transcript_still_holds_the_reveal_request_with_its_answers():
    quiz = open_question().with_answer(STARTER, "Sam", "Tejo").revealing().heard("Sam hat recht.", PLAYERS)

    assert answers_in_reveal(quiz.transcript[-3].text) == [{"name": "Sam", "antwort": "Tejo"}]


def every_step_of_a_quiz():
    return [
        pytest.param(Quiz(STARTER), id="asking"),
        pytest.param(open_question(), id="answering"),
        pytest.param(open_question().revealing(), id="revealing"),
        pytest.param(last_round_revealed(), id="scoring"),
    ]


@pytest.mark.parametrize("quiz", every_step_of_a_quiz())
@pytest.mark.parametrize(
    ("peer_id", "hosts_call", "may_end"),
    [(STARTER, False, True), (OTHER, True, True), (OTHER, False, False)],
    ids=["starter", "host who did not start", "another seat"],
)
def test_the_starter_or_a_host_may_end_the_quiz_in_every_step(quiz, peer_id, hosts_call, may_end):
    assert quiz.may_end(peer_id, hosts_call=hosts_call) is may_end


def test_an_ended_quiz_is_over_and_holds_no_transcript_players_or_answers():
    quiz = open_question().with_answer(STARTER, "Sam", "A").ended()

    assert quiz.is_over
    assert (quiz.transcript, quiz.players, quiz.answers) == ((), frozenset(), ())


def test_nobody_may_answer_reveal_or_end_an_ended_quiz():
    quiz = open_question().ended()

    assert not quiz.may_answer(STARTER)
    assert not quiz.may_reveal(STARTER)
    assert not quiz.may_end(STARTER, hosts_call=True)


@pytest.mark.parametrize(
    ("quiz", "is_opening"),
    [
        pytest.param(Quiz(STARTER), True, id="round one asking"),
        pytest.param(open_question(), False, id="round one answering"),
        pytest.param(open_question().revealing(), False, id="round one revealing"),
        pytest.param(open_question().revealing().heard("Lissabon.", PLAYERS), False, id="round two asking"),
    ],
)
def test_only_the_first_rounds_asking_is_the_opening_run(quiz, is_opening):
    assert quiz.is_opening is is_opening
