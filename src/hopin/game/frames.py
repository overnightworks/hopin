from dataclasses import dataclass
from typing import ClassVar, Literal, get_args

from hopin.game.vocabulary import GamePhase, GameRefusal, GameSignal
from hopin.signaling.frames import PeerId, WireFrame

QuizPhase = Literal[GamePhase.THINKING, GamePhase.ANSWERING]
OtherRefusal = Literal[
    GameRefusal.NO_ACCESS,
    GameRefusal.SIGN_IN_AGAIN,
    GameRefusal.BUDGET_SPENT,
    GameRefusal.ALREADY_ANSWERING,
    GameRefusal.QUIZ_RUNNING,
    GameRefusal.NOT_STARTER,
    GameRefusal.NOT_STARTER_OR_HOST,
    GameRefusal.ANSWER_CLOSED,
    GameRefusal.ANSWER_TOO_LONG,
    GameRefusal.UNAVAILABLE,
]


@dataclass(frozen=True)
class IdlePhaseFrame(WireFrame):
    TYPE: ClassVar[GameSignal] = GameSignal.GAME_PHASE

    phase: Literal[GamePhase.IDLE]


@dataclass(frozen=True)
class QuizPhaseFrame(WireFrame):
    TYPE: ClassVar[GameSignal] = GameSignal.GAME_PHASE

    phase: QuizPhase
    round: int
    rounds: int
    starter: PeerId


@dataclass(frozen=True)
class LineFrame(WireFrame):
    TYPE: ClassVar[GameSignal] = GameSignal.GAME_LINE

    text: str
    voiced: Literal[True] | None = None
    round: int | None = None


@dataclass(frozen=True)
class VoiceFrame(WireFrame):
    """The voice of the line before it."""

    TYPE: ClassVar[GameSignal] = GameSignal.GAME_VOICE

    id: str
    path: str


@dataclass(frozen=True)
class SilentVoiceFrame(WireFrame):
    """The word that no voice follows the line before it."""

    TYPE: ClassVar[GameSignal] = GameSignal.GAME_VOICE


@dataclass(frozen=True)
class AnswerCountFrame(WireFrame):
    """The count for one seat; `own_answer_counted` says its own answer is in it, which an earlier count lacks."""

    TYPE: ClassVar[GameSignal] = GameSignal.GAME_ANSWER_COUNT

    answered: int
    players: int
    own_answer_counted: Literal[True] | None = None


@dataclass(frozen=True)
class BusyRefusedFrame(WireFrame):
    """A busy refusal always carries its wait."""

    TYPE: ClassVar[GameSignal] = GameSignal.GAME_REFUSED

    reason: Literal[GameRefusal.BUSY]
    retry_after_seconds: int


@dataclass(frozen=True)
class RefusedFrame(WireFrame):
    """Any refusal but busy; none of them carries a wait."""

    TYPE: ClassVar[GameSignal] = GameSignal.GAME_REFUSED

    reason: OtherRefusal


@dataclass(frozen=True)
class EndedFrame(WireFrame):
    TYPE: ClassVar[GameSignal] = GameSignal.GAME_ENDED

    leaver: str | None = None


type GameFrame = (
    IdlePhaseFrame
    | QuizPhaseFrame
    | LineFrame
    | VoiceFrame
    | SilentVoiceFrame
    | AnswerCountFrame
    | BusyRefusedFrame
    | RefusedFrame
    | EndedFrame
)

# The frames the generator renders into the game's own browser wire, in the order they appear there.
BROWSER_FRAMES = get_args(GameFrame.__value__)
