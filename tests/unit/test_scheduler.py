import asyncio

import pytest
import structlog.testing

from hopin.signaling.scheduler import AsyncioScheduler

WAIT_CAP_SECONDS = 5


class Boom(Exception):
    pass


@pytest.fixture
def scheduler():
    return AsyncioScheduler()


async def wait_for(event: asyncio.Event) -> None:
    await asyncio.wait_for(event.wait(), WAIT_CAP_SECONDS)


class TestAsyncioScheduler:
    @pytest.mark.asyncio
    async def test_call_later_runs_the_callback_once_it_is_due(self, scheduler):
        ran = asyncio.Event()

        async def callback():
            ran.set()

        scheduler.call_later(0, callback)

        await wait_for(ran)

    @pytest.mark.asyncio
    async def test_cancelled_timer_never_runs_its_callback(self, scheduler):
        ran = []
        later_ran = asyncio.Event()

        async def cancelled_callback():
            ran.append("cancelled")

        async def later_callback():
            later_ran.set()

        scheduler.call_later(0, cancelled_callback).cancel()
        scheduler.call_later(0, later_callback)

        await wait_for(later_ran)
        assert ran == []

    @pytest.mark.asyncio
    async def test_cancel_from_inside_the_running_callback_does_not_interrupt_it(self, scheduler):
        finished = asyncio.Event()
        handles = []

        async def callback():
            handles[0].cancel()
            await asyncio.sleep(0)
            finished.set()

        handles.append(scheduler.call_later(0, callback))

        await wait_for(finished)

    @pytest.mark.asyncio
    async def test_now_advances_with_the_event_loop_clock(self, scheduler):
        assert scheduler.now() == asyncio.get_running_loop().time()

    @pytest.mark.asyncio
    async def test_raising_callback_is_logged_as_timer_failed(self, scheduler):
        raised = asyncio.Event()

        async def callback():
            raised.set()
            raise Boom

        with structlog.testing.capture_logs() as logs:
            scheduler.call_later(0, callback)
            await wait_for(raised)
            await asyncio.sleep(0)

        [failure] = [log for log in logs if log["event"] == "timer_failed"]
        assert failure["log_level"] == "error"
        assert isinstance(failure["exc_info"], Boom)

    @pytest.mark.asyncio
    async def test_cancelled_callback_task_is_not_logged_as_a_failure(self, scheduler):
        started = asyncio.Event()

        async def callback():
            started.set()
            await asyncio.Event().wait()

        with structlog.testing.capture_logs() as logs:
            scheduler.call_later(0, callback)
            await wait_for(started)
            [timer_task] = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            timer_task.cancel()
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        assert [log for log in logs if log["event"] == "timer_failed"] == []
