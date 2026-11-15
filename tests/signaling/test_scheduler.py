import asyncio

import pytest
import structlog.testing
from logline import current_traceparent, unit_trace

from hopin.signaling.scheduler import AsyncioScheduler

WAIT_CAP_SECONDS = 5


class TimerError(Exception):
    pass


@pytest.fixture
def scheduler():
    return AsyncioScheduler()


def trace_id_now() -> str | None:
    traceparent = current_traceparent()
    return None if traceparent is None else traceparent.split("-")[1]


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
    async def test_a_timer_runs_in_a_trace_of_its_own_not_the_one_that_scheduled_it(self, scheduler):
        ran = asyncio.Event()
        traces: dict[str, str | None] = {}

        async def callback():
            traces["timer"] = trace_id_now()
            ran.set()

        with unit_trace(None):
            traces["scheduling"] = trace_id_now()
            scheduler.call_later(0, callback)
        await wait_for(ran)

        assert traces["timer"] is not None
        assert traces["timer"] != traces["scheduling"]

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
    async def test_now_reads_the_event_loop_clock(self, scheduler):
        loop = asyncio.get_running_loop()
        before = loop.time()
        reading = scheduler.now()
        assert before <= reading <= loop.time()

    @pytest.mark.asyncio
    async def test_raising_callback_is_logged_as_timer_failed(self, scheduler):
        raised = asyncio.Event()

        async def callback():
            raised.set()
            raise TimerError

        with structlog.testing.capture_logs() as logs:
            scheduler.call_later(0, callback)
            await wait_for(raised)
            await asyncio.sleep(0)

        [failure] = [log for log in logs if log["event"] == "timer.failed"]
        assert failure["log_level"] == "error"
        assert isinstance(failure["exc_info"], TimerError)

    @pytest.mark.asyncio
    async def test_a_failing_timer_logs_its_failure_in_its_own_trace_not_the_one_that_scheduled_it(self, scheduler):
        raised = asyncio.Event()
        traces: dict[str, str | None] = {}

        async def callback():
            traces["timer"] = trace_id_now()
            raised.set()
            raise TimerError

        def stamp_trace(_logger, _method, event_dict):
            event_dict["trace_id"] = trace_id_now()
            return event_dict

        with structlog.testing.capture_logs(processors=[stamp_trace]) as logs:
            with unit_trace(None):
                traces["scheduling"] = trace_id_now()
                scheduler.call_later(0, callback)
            await wait_for(raised)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        [failure] = [log for log in logs if log["event"] == "timer.failed"]
        assert failure["trace_id"] == traces["timer"]
        assert failure["trace_id"] != traces["scheduling"]

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

        assert [log for log in logs if log["event"] == "timer.failed"] == []
