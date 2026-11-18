import asyncio
from enum import StrEnum
from functools import partial
from typing import TYPE_CHECKING, Protocol

import structlog
from logline import unit_trace

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class TimerHandle(Protocol):
    def cancel(self) -> None:
        """Stop a callback that has not started; never interrupts a running one."""


class Clock(Protocol):
    def now(self) -> float:
        """Monotonic seconds."""


class Scheduler(Clock, Protocol):
    """Timers owned by a live connection or by the process: the socket recheck, the hearing socket's recheck, the
    session sweep and the renewal of an agent's hearing token after the call that found it due. Every other timer
    outlives a connection and is a deadline of a named kind on `Deadlines`."""

    def call_later(self, delay_seconds: float, callback: Callable[[], Awaitable[None]]) -> TimerHandle:
        """Run the callback once the delay is due."""


class DeadlineKind(StrEnum):
    HOST_GRACE = "host_grace"
    RESUME_WINDOW = "resume_window"
    HEARING_LEASE = "hearing_lease"
    LEASE = "lease"
    INVITATION = "invitation"
    SENTENCE_END = "sentence_end"
    LOOK_ANSWER = "look_answer"
    WINDOW_RECOVERY = "window_recovery"
    GAME_RUN = "game_run"
    GAME_VOICE = "game_voice"


class Deadlines(Clock, Protocol):
    """Deadlines of a named kind that outlive a connection; setting a key again replaces its deadline."""

    def register(self, kind: DeadlineKind, handler: Callable[[str], Awaitable[None]]) -> None:
        """Set the one handler that runs when a deadline of the kind falls due."""

    def set(self, kind: DeadlineKind, key: str, due_in: float) -> None:
        """Run the kind's handler with the key once `due_in` seconds have passed."""

    def cancel(self, kind: DeadlineKind, key: str) -> None:
        """Delete the deadline; a handler that has started is left alone."""


class InMemoryDeadlines:
    """Deadlines held in this process and run by its scheduler."""

    def __init__(self, scheduler: Scheduler) -> None:
        self._scheduler = scheduler
        self._handlers: dict[DeadlineKind, Callable[[str], Awaitable[None]]] = {}
        self._pending: dict[tuple[DeadlineKind, str], tuple[int, TimerHandle]] = {}
        self._sets = 0

    def now(self) -> float:
        return self._scheduler.now()

    def register(self, kind: DeadlineKind, handler: Callable[[str], Awaitable[None]]) -> None:
        if kind in self._handlers:
            message = f"{kind} already has a handler"
            raise ValueError(message)
        self._handlers[kind] = handler

    def set(self, kind: DeadlineKind, key: str, due_in: float) -> None:
        self.cancel(kind, key)
        self._sets += 1
        handle = self._scheduler.call_later(due_in, partial(self._due, kind, key, self._sets))
        self._pending[kind, key] = (self._sets, handle)

    def cancel(self, kind: DeadlineKind, key: str) -> None:
        pending = self._pending.pop((kind, key), None)
        if pending is not None:
            pending[1].cancel()

    async def _due(self, kind: DeadlineKind, key: str, set_number: int) -> None:
        # A callback that started before its cancel or replacement still arrives here, and finds its deadline gone.
        pending = self._pending.get((kind, key))
        if pending is None or pending[0] != set_number:
            return
        del self._pending[kind, key]
        await self._handlers[kind](key)


class AsyncioScheduler:
    def __init__(self) -> None:
        self._running: set[asyncio.Task[None]] = set()

    def now(self) -> float:
        return asyncio.get_running_loop().time()

    def call_later(self, delay_seconds: float, callback: Callable[[], Awaitable[None]]) -> TimerHandle:
        return asyncio.get_running_loop().call_later(delay_seconds, self._start, callback)

    def _start(self, callback: Callable[[], Awaitable[None]]) -> None:
        task = asyncio.get_running_loop().create_task(self._run(callback))
        self._running.add(task)
        task.add_done_callback(self._finished)

    @staticmethod
    async def _run(callback: Callable[[], Awaitable[None]]) -> None:
        # A timer is a unit of its own: the context it copied at scheduling carries the scheduler's trace.
        with unit_trace(None):
            try:
                await callback()
            except Exception as error:
                logger.error("timer.failed", exc_info=error)

    def _finished(self, task: asyncio.Task[None]) -> None:
        self._running.discard(task)
