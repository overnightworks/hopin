"""Steps that run in a turn of their own, so their caller never waits on them: the set keeps each task until it ends,
and a step that fails is written by the caller's own logger."""

import asyncio
from collections.abc import Callable, Coroutine
from functools import partial


class OwnTurns:
    def __init__(self) -> None:
        self._running: set[asyncio.Task[None]] = set()

    def start(
        self,
        step: Coroutine[None, None, None],
        log_failure: Callable[[BaseException], None],
        *,
        name: str | None = None,
    ) -> None:
        task = asyncio.create_task(step, name=name)
        self._running.add(task)
        task.add_done_callback(partial(self._ended, log_failure))

    def _ended(self, log_failure: Callable[[BaseException], None], task: asyncio.Task[None]) -> None:
        self._running.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            log_failure(error)

    def cancel(self) -> None:
        for task in self._running:
            task.cancel()

    async def wait(self) -> None:
        await asyncio.gather(*self._running, return_exceptions=True)
