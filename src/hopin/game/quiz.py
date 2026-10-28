"""A quiz's rules: its rounds, its transcript and who may reveal. Pure; the table runs it."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from hopin.signaling.frames import PeerId

QUIZ_ROUNDS: Final = 5
NEXT_QUESTION_REQUEST: Final = "Stelle die nächste Quizfrage."
REVEAL_REQUEST: Final = "Löse die Frage auf."
FINAL_SCORE_REQUEST: Final = "Das war die letzte Frage. Nenne den Endstand."


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
    SCORING = auto()
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
    def awaits_game_master(self) -> bool:
        return self.step in {QuizStep.ASKING, QuizStep.REVEALING, QuizStep.SCORING}

    @property
    def is_over(self) -> bool:
        return self.step is QuizStep.OVER

    def may_reveal(self, peer_id: PeerId) -> bool:
        return peer_id == self.starter and self.step is QuizStep.ANSWERING

    def revealing(self) -> Quiz:
        transcript = (*self.transcript, Turn(Speaker.HOPIN, REVEAL_REQUEST))
        return Quiz(self.starter, step=QuizStep.REVEALING, round=self.round, transcript=transcript)

    def answered(self, line: str) -> Quiz:
        """The quiz once the game master's line answers the step's request, with the request that follows."""
        transcript = (*self.transcript, Turn(Speaker.GAME_MASTER, line))
        match self.step:
            case QuizStep.ASKING:
                return Quiz(self.starter, step=QuizStep.ANSWERING, round=self.round, transcript=transcript)
            case QuizStep.REVEALING if self.round < QUIZ_ROUNDS:
                return self._asking_next_round(transcript)
            case QuizStep.REVEALING:
                transcript = (*transcript, Turn(Speaker.HOPIN, FINAL_SCORE_REQUEST))
                return Quiz(self.starter, step=QuizStep.SCORING, round=self.round, transcript=transcript)
            case _:
                return Quiz(self.starter, step=QuizStep.OVER, round=self.round, transcript=transcript)

    def _asking_next_round(self, transcript: tuple[Turn, ...]) -> Quiz:
        transcript = (*transcript, Turn(Speaker.HOPIN, NEXT_QUESTION_REQUEST))
        return Quiz(self.starter, step=QuizStep.ASKING, round=self.round + 1, transcript=transcript)
