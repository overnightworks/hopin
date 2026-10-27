from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from hopin.config.constants import GameRefusal


@dataclass(frozen=True)
class Question:
    text: str


@dataclass(frozen=True)
class Refused:
    reason: GameRefusal
    retry_after_seconds: int | None = None


type GameOutcome = Question | Refused


class GameMaster(Protocol):
    async def ask(self, starter: WebSocket, questions: tuple[str, ...]) -> GameOutcome:
        """The next question on the starter's access, after `questions`, or why there is none.

        Raises nothing but `CancelledError`; a cancelled call has closed its connection when it ends.
        """
