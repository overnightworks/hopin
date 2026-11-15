import asyncio
from typing import TYPE_CHECKING, Protocol

import structlog
from logline import unit_trace

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class TimerHandle(Protocol):
    def cancel(self) -> None:
        """Stop a callback that has not started; never interrupts a running one."""


class Scheduler(Protocol):
    def now(self) -> float:
        """Monotonic seconds."""

    def call_later(self, delay_seconds: float, callback: Callable[[], Awaitable[None]]) -> TimerHandle:
        """Run the callback once the delay is due."""


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
