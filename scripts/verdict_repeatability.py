# ruff: noqa: T201 - this command's job is printing its report
"""Run the same recorded answers through the configured turn provider several times and report how often the quiz
master's verdicts agree.

It costs real model calls and runs by hand, never in CI: `make verdict-repeatability RUNS=10`. The environment is the
server's (`HOPIN_AGENT__...`, the gateway key file); the turns run on the configured gateway as a throwaway identity
that belongs to the configured API group. The command exits non-zero when a verdict's agreement is below `--minimum`
or a run gave no verdicts.
"""

import argparse
import asyncio
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

from agents.backend import TurnPerson
from agents.turn import Principal
from starlette.requests import HTTPConnection

from hopin.game.quiz import Judgement, Quiz, Turn
from hopin.game.table import Line
from hopin.signaling.frames import PeerId

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hopin.game.table import GameOutcome

RECORDING: Final = Path(__file__).parent / "verdict_recording.json"
IDENTITY: Final = "hopin-verdict-repeatability"
LANGUAGE: Final = "de"


class Asker(Protocol):
    async def ask(self, starter: HTTPConnection, transcript: tuple[Turn, ...]) -> GameOutcome: ...


@dataclass(frozen=True)
class RecordedAnswer:
    name: str
    text: str


@dataclass(frozen=True)
class RecordedCase:
    question: str
    answers: tuple[RecordedAnswer, ...]


@dataclass(frozen=True)
class VerdictAgreement:
    """How the runs judged one answer: the most frequent judgement and in how many runs it came."""

    case: int
    answer: str
    judgement: Judgement | None
    runs_agreeing: int
    runs: int

    @property
    def share(self) -> float:
        return self.runs_agreeing / self.runs


def recorded_cases(recording: Path = RECORDING) -> tuple[RecordedCase, ...]:
    document = json.loads(recording.read_text(encoding="utf-8"))
    return tuple(
        RecordedCase(
            entry["question"], tuple(RecordedAnswer(answer["name"], answer["antwort"]) for answer in entry["answers"])
        )
        for entry in document
    )


def reveal_transcript(case: RecordedCase) -> tuple[Turn, ...]:
    """The transcript of the reveal run for the case, built by the quiz itself as a table would."""
    seats = [PeerId(f"seat-{number}") for number in range(len(case.answers))]
    quiz = Quiz.opening(seats[0], ("Allgemeinwissen",) * 5).heard(case.question, frozenset(seats))
    for seat, answer in zip(seats, case.answers, strict=True):
        quiz = quiz.with_answer(seat, answer.name, answer.text)
    return quiz.revealing().transcript


async def agreements(
    game_master: Asker, starter: HTTPConnection, cases: Sequence[RecordedCase], runs: int
) -> list[VerdictAgreement]:
    """Ask the reveal of every case `runs` times; a run without verdicts counts as no judgement."""
    report = []
    for index, case in enumerate(cases, start=1):
        judged: list[list[Judgement | None]] = [[] for _ in case.answers]
        for _ in range(runs):
            outcome = await game_master.ask(starter, reveal_transcript(case))
            by_number = (
                {verdict.answer: verdict.judgement for verdict in outcome.verdicts} if isinstance(outcome, Line) else {}
            )
            for number, judgements in enumerate(judged, start=1):
                judgements.append(by_number.get(number))
        for answer, judgements in zip(case.answers, judged, strict=True):
            judgement, agreeing = Counter(judgements).most_common(1)[0]
            report.append(VerdictAgreement(index, f"{answer.name}: {answer.text}", judgement, agreeing, runs))
    return report


def printed(report: Sequence[VerdictAgreement]) -> str:
    return "\n".join(
        f"case {line.case}  {line.answer}  {line.judgement.value if line.judgement else 'no verdict'}  "
        f"{line.runs_agreeing}/{line.runs}"
        for line in report
    )


def passes(report: Sequence[VerdictAgreement], minimum: float) -> bool:
    return all(line.judgement is not None and line.share >= minimum for line in report)


class ThrowawayStarter:
    """Stands for the starter of a run: a fixed identity of the API group, in German."""

    def __init__(self, api_group: str) -> None:
        self._person = TurnPerson(Principal(iss=IDENTITY, sub=IDENTITY), "user", self._groups)
        self._api_group = api_group

    async def _groups(self) -> frozenset[str]:
        return frozenset({self._api_group})

    async def language_of(self, _starter: HTTPConnection) -> str:
        return LANGUAGE

    async def person_of(self, _starter: HTTPConnection) -> TurnPerson:
        return self._person


async def run(runs: int, minimum: float) -> int:
    from hopin.agent.backend import QuizGameMaster, gateway_backend
    from hopin.agent.settings import AgentSettings

    settings = AgentSettings()
    game_master = QuizGameMaster(gateway_backend(settings), ThrowawayStarter(settings.agent.api_group))
    try:
        report = await agreements(game_master, HTTPConnection({"type": "http", "headers": []}), recorded_cases(), runs)
    finally:
        await game_master.aclose()
    print(printed(report))
    return 0 if passes(report, minimum) else 1


def main(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10, help="how many times each recorded reveal is asked")
    parser.add_argument("--minimum", type=float, default=1.0, help="the least share of runs that must agree")
    parsed = parser.parse_args(arguments)
    return asyncio.run(run(parsed.runs, parsed.minimum))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
