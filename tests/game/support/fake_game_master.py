import asyncio
from collections import deque
from dataclasses import dataclass, field

from hopin.game.table import Question

DEFAULT_QUESTION = "Wie heißt die Hauptstadt von Portugal?"


@dataclass(eq=False)
class HeldAsk:
    """One held call of the fake game master: it answers once released; its cancellation cleanup can be held too."""

    hold_cleanup: bool = False
    started: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    cleanup_started: asyncio.Event = field(default_factory=asyncio.Event)
    cleanup_release: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass(eq=False)
class AskCall:
    starter: object
    questions: tuple[str, ...]
    cancelled: bool = False
    finished: bool = False


class FakeGameMaster:
    """Answers each call with the next scripted outcome, or raises it when it is an exception; a call can be held."""

    def __init__(self) -> None:
        self._outcomes: deque = deque()
        self._holds: deque[HeldAsk] = deque()
        self._issued_holds: list[HeldAsk] = []
        self.calls: list[AskCall] = []

    def answer_next(self, *outcomes) -> None:
        self._outcomes.extend(outcomes)

    def hold_next(self, *, hold_cleanup: bool = False) -> HeldAsk:
        held = HeldAsk(hold_cleanup=hold_cleanup)
        self._holds.append(held)
        self._issued_holds.append(held)
        return held

    def release_every_hold(self) -> None:
        """Let every held call and every held cleanup finish, so a teardown cannot wait on one forever."""
        for held in self._issued_holds:
            held.release.set()
            held.cleanup_release.set()

    async def ask(self, starter, questions):
        call = AskCall(starter, questions)
        self.calls.append(call)
        held = self._holds.popleft() if self._holds else None
        try:
            if held is not None:
                held.started.set()
                await held.release.wait()
            outcome = self._outcomes.popleft() if self._outcomes else Question(DEFAULT_QUESTION)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        except asyncio.CancelledError:
            call.cancelled = True
            if held is not None and held.hold_cleanup:
                held.cleanup_started.set()
                await held.cleanup_release.wait()
            raise
        finally:
            call.finished = True
