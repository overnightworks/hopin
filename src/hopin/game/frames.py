from dataclasses import dataclass
from typing import ClassVar, Literal, get_args

from hopin.game.vocabulary import GamePhase, GameRefusal, GameSignal
from hopin.signaling.frames import TYPE_KEY, JsonObject, PeerId, with_present

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
class IdlePhaseFrame:
    TYPE: ClassVar[GameSignal] = GameSignal.GAME_PHASE

    phase: Literal[GamePhase.IDLE]

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "phase": self.phase.value}


@dataclass(frozen=True)
class QuizPhaseFrame:
    TYPE: ClassVar[GameSignal] = GameSignal.GAME_PHASE

    phase: QuizPhase
    round: int
    rounds: int
    starter: PeerId

    def to_json(self) -> JsonObject:
        return {
            TYPE_KEY: self.TYPE.value,
            "phase": self.phase.value,
            "round": self.round,
            "rounds": self.rounds,
            "starter": self.starter,
        }


@dataclass(frozen=True)
class LineFrame:
    TYPE: ClassVar[GameSignal] = GameSignal.GAME_LINE

    text: str
    voiced: Literal[True] | None = None
    round: int | None = None

    def to_json(self) -> JsonObject:
        frame: JsonObject = {TYPE_KEY: self.TYPE.value, "text": self.text}
        return with_present(frame, voiced=self.voiced, round=self.round)


@dataclass(frozen=True)
class VoiceFrame:
    """The voice of the line before it."""

    TYPE: ClassVar[GameSignal] = GameSignal.GAME_VOICE

    id: str
    path: str

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "id": self.id, "path": self.path}


@dataclass(frozen=True)
class SilentVoiceFrame:
    """The word that no voice follows the line before it."""

    TYPE: ClassVar[GameSignal] = GameSignal.GAME_VOICE

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value}


@dataclass(frozen=True)
class AnswerCountFrame:
    """The count for one seat; `own_answer_counted` says its own answer is in it, which an earlier count lacks."""

    TYPE: ClassVar[GameSignal] = GameSignal.GAME_ANSWER_COUNT

    answered: int
    players: int
    own_answer_counted: Literal[True] | None = None

    def to_json(self) -> JsonObject:
        return with_present(
            {TYPE_KEY: self.TYPE.value, "answered": self.answered, "players": self.players},
            own_answer_counted=self.own_answer_counted,
        )


@dataclass(frozen=True)
class BusyRefusedFrame:
    """A busy refusal always carries its wait."""

    TYPE: ClassVar[GameSignal] = GameSignal.GAME_REFUSED

    reason: Literal[GameRefusal.BUSY]
    retry_after_seconds: int

    def to_json(self) -> JsonObject:
        return {
            TYPE_KEY: self.TYPE.value,
            "reason": self.reason.value,
            "retry_after_seconds": self.retry_after_seconds,
        }


@dataclass(frozen=True)
class RefusedFrame:
    """Any refusal but busy; none of them carries a wait."""

    TYPE: ClassVar[GameSignal] = GameSignal.GAME_REFUSED

    reason: OtherRefusal

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "reason": self.reason.value}


@dataclass(frozen=True)
class EndedFrame:
    TYPE: ClassVar[GameSignal] = GameSignal.GAME_ENDED

    leaver: str | None = None

    def to_json(self) -> JsonObject:
        frame: JsonObject = {TYPE_KEY: self.TYPE.value}
        return with_present(frame, leaver=self.leaver)


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
