"""A quiz's rules: its rounds, its transcript and who may reveal. Pure; the table runs it."""

from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from hopin.signaling.frames import PeerId

QUIZ_ROUNDS: Final = 1
NEXT_QUESTION_REQUEST: Final = "Stelle die nächste Quizfrage."
REVEAL_REQUEST: Final = "Löse die Frage auf."


class Speaker(Enum):
    HOPIN = auto()
    GAME_MASTER = auto()


@dataclass(frozen=True)
class Turn:
    speaker: Speaker
    text: str


class QuizStep(Enum):
    ASKING = auto()
    ANSWERING = auto()
    REVEALING = auto()
    OVER = auto()


@dataclass(frozen=True)
class Quiz:
    """One quiz of a room: the transcript ends with the request of the run the step waits for."""

    starter: PeerId
    step: QuizStep = QuizStep.ASKING
    round: int = 1
    transcript: tuple[Turn, ...] = (Turn(Speaker.HOPIN, NEXT_QUESTION_REQUEST),)

    @property
    def is_asking(self) -> bool:
        return self.step is QuizStep.ASKING

    @property
    def is_over(self) -> bool:
        return self.step is QuizStep.OVER

    def may_reveal(self, peer_id: PeerId) -> bool:
        return peer_id == self.starter and self.step is QuizStep.ANSWERING

    def revealing(self) -> Quiz:
        return replace(
            self, step=QuizStep.REVEALING, transcript=(*self.transcript, Turn(Speaker.HOPIN, REVEAL_REQUEST))
        )

    def answered(self, line: str) -> Quiz:
        """The quiz once the game master's line answers the step's request."""
        step = QuizStep.ANSWERING if self.is_asking else QuizStep.OVER
        return replace(self, step=step, transcript=(*self.transcript, Turn(Speaker.GAME_MASTER, line)))
