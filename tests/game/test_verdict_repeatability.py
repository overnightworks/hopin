"""The repeatability command, run against a scripted provider instead of the configured one."""

import pytest
from starlette.requests import HTTPConnection

from hopin.agent.backend import QuizGameMaster
from hopin.agent.verdict_check import ThrowawayStarter
from hopin.game.quiz import Judgement
from hopin.game.verdict_repeatability import (
    RecordedAnswer,
    RecordedCase,
    agreements,
    passes,
    printed,
    recorded_cases,
    reveal_transcript,
)
from tests.game.support.fake_agent_backend import FakeAgentBackend, Succeeds

pytestmark = pytest.mark.asyncio

CASE = RecordedCase("Frage?", (RecordedAnswer("Anna", "Tejo"), RecordedAnswer("Ben", "Rhein")))
STARTER = HTTPConnection({"type": "http", "headers": []})


def judging(first, second):
    return Succeeds(
        "Auflösung.",
        verdicts=(
            {"nr": 1, "judgement": first.value},
            {"nr": 2, "judgement": second.value},
        ),
    )


async def agreement_over(*runs):
    backend = FakeAgentBackend()
    backend.answer_next(*runs)
    game_master = QuizGameMaster(backend, ThrowawayStarter("/api"))
    return await agreements(game_master, STARTER, [CASE], len(runs))


async def test_the_report_counts_in_how_many_runs_the_most_frequent_verdict_of_each_answer_came():
    """GM-41: The report counts in how many runs the most frequent verdict of each answer came."""
    right, wrong = Judgement.CORRECT, Judgement.WRONG

    report = await agreement_over(
        judging(right, wrong), judging(right, wrong), judging(right, wrong), judging(wrong, wrong)
    )

    assert [(line.judgement, line.runs_agreeing, line.runs) for line in report] == [(right, 3, 4), (wrong, 4, 4)]
    assert not passes(report, 1.0)
    assert passes(report, 0.75)


async def test_runs_that_give_no_verdict_are_reported_and_never_pass():
    report = await agreement_over(Succeeds("Auflösung.", verdicts=({"nr": 1, "judgement": "maybe"},)))

    assert [line.judgement for line in report] == [None, None]
    assert "no verdict" in printed(report)
    assert not passes(report, 0.0)


async def test_a_single_run_without_a_verdict_fails_the_report_however_low_the_minimum():
    right, wrong = Judgement.CORRECT, Judgement.WRONG
    malformed = Succeeds("Auflösung.", verdicts=({"nr": 1, "judgement": "maybe"},))

    report = await agreement_over(judging(right, wrong), judging(right, wrong), judging(right, wrong), malformed)

    assert [line.judgement for line in report] == [right, wrong]
    assert not passes(report, 0.5)


async def test_the_printed_report_names_how_many_runs_gave_no_verdict():
    right, wrong = Judgement.CORRECT, Judgement.WRONG
    malformed = Succeeds("Auflösung.", verdicts=({"nr": 1, "judgement": "maybe"},))

    with_malformed = await agreement_over(judging(right, wrong), malformed)
    without_malformed = await agreement_over(judging(right, wrong))

    assert "(1 run without verdict)" in printed(with_malformed)
    assert "without verdict" not in printed(without_malformed)


async def test_every_run_asks_the_reveal_request_the_quiz_itself_builds_for_the_recorded_answers():
    backend = FakeAgentBackend()
    game_master = QuizGameMaster(backend, ThrowawayStarter("/api"))

    await agreements(game_master, STARTER, [CASE], 2)

    asked = [[message.text for message in run.request.turn.messages] for run in backend.runs]
    assert asked == [[turn.text for turn in reveal_transcript(CASE)]] * 2
    assert all(run.groups == {"/api"} for run in backend.runs)


def test_the_shipped_recording_holds_answers_for_every_case():
    assert all(case.answers for case in recorded_cases())


def test_the_recording_ships_as_package_data_with_answers_for_every_case():
    cases = recorded_cases()

    assert cases
    assert all(case.answers for case in cases)
