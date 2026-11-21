"""Run the same recorded answers through a game master several times and report how often the quiz master's verdicts
agree (GM-41). The report logic is pure; `hopin.agent.verdict_check` supplies the configured provider."""

import json
from collections import Counter
from dataclasses import dataclass
from importlib.resources import files
from typing import TYPE_CHECKING, Protocol

from starlette.requests import HTTPConnection

from hopin.game.quiz import Judgement, Quiz, Turn
from hopin.game.table import Line
from hopin.signaling.frames import PeerId

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hopin.game.table import GameOutcome


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
    runs_without_verdict: int = 0

    @property
    def share(self) -> float:
        return self.runs_agreeing / self.runs


def recorded_cases() -> tuple[RecordedCase, ...]:
    document = json.loads(files("hopin.game").joinpath("verdict_recording.json").read_text(encoding="utf-8"))
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
                {verdict.nr: verdict.judgement for verdict in outcome.verdicts} if isinstance(outcome, Line) else {}
            )
            for number, judgements in enumerate(judged, start=1):
                judgements.append(by_number.get(number))
        for answer, judgements in zip(case.answers, judged, strict=True):
            judgement, agreeing = Counter(judgements).most_common(1)[0]
            report.append(
                VerdictAgreement(
                    index, f"{answer.name}: {answer.text}", judgement, agreeing, runs, judgements.count(None)
                )
            )
    return report


def printed(report: Sequence[VerdictAgreement]) -> str:
    return "\n".join(
        f"case {line.case}  {line.answer}  {line.judgement.value if line.judgement else 'no verdict'}  "
        f"{line.runs_agreeing}/{line.runs}{_without_verdict(line)}"
        for line in report
    )


def _without_verdict(line: VerdictAgreement) -> str:
    if not line.runs_without_verdict:
        return ""
    noun = "run" if line.runs_without_verdict == 1 else "runs"
    return f"  ({line.runs_without_verdict} {noun} without verdict)"


def passes(report: Sequence[VerdictAgreement], minimum: float) -> bool:
    return all(line.runs_without_verdict == 0 and line.share >= minimum for line in report)
