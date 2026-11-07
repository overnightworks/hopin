"""An agent backend that exists only in a test: it asks for the person's groups, as the real one does, and answers
from a script.

It stands where the client's `GatewayBackend` stands, at `TurnEvent` and `TurnStopped` level. The script and the
record are shared by all runs and all threads.
"""

import asyncio
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from agent_runtime_client.backend import AgentBackend, TurnStopKind, TurnStopped
from agent_runtime_client.turn import FailureCode, RejectionCode, RunFile
from agent_runtime_client.turn_events import AssistantTextEvent, FinalEvent, ToolCallEvent

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from agent_runtime_client.agents import TurnRequest
    from agent_runtime_client.backend import TurnEvent, TurnPerson
    from agent_runtime_client.turn import AgentRole, Principal

DEFAULT_QUESTION = "Welcher Fluss fließt durch Lissabon?"
SIGNAL_CAP_SECONDS = 10


class Signal:
    """Set once from any thread; waited for from another thread or from a coroutine on any loop."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._set = threading.Event()
        self._waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = []

    def set(self) -> None:
        with self._guard:
            self._set.set()
            waiters, self._waiters = self._waiters, []
        for loop, event in waiters:
            loop.call_soon_threadsafe(event.set)

    def is_set(self) -> bool:
        return self._set.is_set()

    def wait(self) -> bool:
        return self._set.wait(SIGNAL_CAP_SECONDS)

    async def wait_async(self) -> None:
        event = asyncio.Event()
        with self._guard:
            if self._set.is_set():
                return
            self._waiters.append((asyncio.get_running_loop(), event))
        await event.wait()


@dataclass(frozen=True)
class SeenRun:
    """One run that reached the backend: who it was for, the groups they hold and what it asked."""

    principal: Principal
    role: AgentRole
    groups: frozenset[str]
    request: TurnRequest
    closed: Signal = field(default_factory=Signal, compare=False)


@dataclass(frozen=True)
class Succeeds:
    """Ends with `text`; `said` is what the agent spoke on the way, before the end."""

    text: str = DEFAULT_QUESTION
    files: tuple[RunFile, ...] = ()
    said: tuple[str, ...] = ()


@dataclass(frozen=True)
class Rejects:
    code: RejectionCode
    retry_after_s: int | None = None
    detail: str | None = None


@dataclass(frozen=True)
class Fails:
    code: FailureCode = FailureCode.PROVIDER_ERROR


@dataclass(frozen=True)
class CallsATool:
    pass


@dataclass(frozen=True)
class EndsEarly:
    """The generator ends without a `FinalEvent`."""


@dataclass(frozen=True)
class SaysThenStops:
    """Speaks `text`, then the turn ends short with `stop`."""

    text: str
    stop: Rejects | Fails


@dataclass(frozen=True, eq=False)
class Holds:
    """Starts the run, then waits until released and answers `then`."""

    then: Succeeds | Fails = Succeeds()
    started: Signal = field(default_factory=Signal)
    release: Signal = field(default_factory=Signal)


type Answer = Succeeds | Rejects | Fails | CallsATool | EndsEarly | SaysThenStops | Holds


class FakeAgentBackend(AgentBackend):
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._script: deque[Answer] = deque()
        self._runs: list[SeenRun] = []

    def answer_next(self, *answers: Answer) -> None:
        with self._guard:
            self._script.extend(answers)

    @property
    def queued_answers(self) -> tuple[Answer, ...]:
        with self._guard:
            return tuple(self._script)

    def reset(self) -> None:
        """Drops the queued answers and the recorded runs."""
        with self._guard:
            self._script.clear()
            self._runs.clear()

    @property
    def runs(self) -> list[SeenRun]:
        with self._guard:
            return list(self._runs)

    async def run(self, request: TurnRequest, person: TurnPerson) -> AsyncGenerator[TurnEvent]:
        seen = SeenRun(person.principal, person.role, await person.groups(), request)
        with self._guard:
            self._runs.append(seen)
            answer = self._script.popleft() if self._script else Succeeds()
        try:
            async for event in _events_of(answer):
                yield event
        finally:
            seen.closed.set()


async def _events_of(answer: Answer) -> AsyncGenerator[TurnEvent]:
    match answer:
        case Succeeds(text=text, files=files, said=said):
            for spoken in said:
                yield AssistantTextEvent(text=spoken)
            yield FinalEvent(text=text, files=files)
        case Rejects(code=code, retry_after_s=retry_after_s, detail=detail):
            raise TurnStopped(TurnStopKind.REFUSED, code, retry_after_s=retry_after_s, detail=detail)
        case Fails(code=code):
            raise TurnStopped(TurnStopKind.FAILED, code)
        case CallsATool():
            yield ToolCallEvent(tool_use_id="call-1", name="lookup")
        case SaysThenStops(text=text, stop=stop):
            yield AssistantTextEvent(text=text)
            async for event in _events_of(stop):
                yield event
        case Holds(then=then, started=started, release=release):
            started.set()
            await release.wait_async()
            async for event in _events_of(then):
                yield event
        case EndsEarly():
            return
