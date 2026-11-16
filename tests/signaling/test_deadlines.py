import pytest

from hopin.signaling.scheduler import DeadlineKind

pytestmark = [pytest.mark.asyncio]

KEY = "room"
OTHER_KIND = DeadlineKind.RESUME_WINDOW
KIND = DeadlineKind.HOST_GRACE


@pytest.fixture
def due():
    return []


@pytest.fixture(autouse=True)
def handled(deadlines, due):
    async def handler(key):
        due.append(key)

    deadlines.register(KIND, handler)


class TestInMemoryDeadlines:
    async def test_a_deadline_runs_its_kinds_handler_with_its_key_once_due(self, deadlines, scheduler, due):
        deadlines.set(KIND, KEY, 10)

        await scheduler.advance(9)
        before = list(due)
        await scheduler.advance(1)

        assert (before, due) == ([], [KEY])

    async def test_a_cancelled_deadline_never_runs(self, deadlines, scheduler, due):
        deadlines.set(KIND, KEY, 10)
        deadlines.cancel(KIND, KEY)

        await scheduler.advance(10)

        assert due == []

    async def test_setting_a_key_again_replaces_its_deadline(self, deadlines, scheduler, due):
        deadlines.set(KIND, KEY, 10)
        deadlines.set(KIND, KEY, 20)

        await scheduler.advance(10)
        before = list(due)
        await scheduler.advance(10)

        assert (before, due) == ([], [KEY])

    async def test_a_handler_that_started_before_its_cancel_finds_the_deadline_gone(self, deadlines, scheduler, due):
        deadlines.set(KIND, KEY, 10)
        deadlines.cancel(KIND, KEY)

        await scheduler.run_cancelled()

        assert due == []

    async def test_a_handler_that_started_before_its_replacement_finds_the_deadline_replaced(
        self, deadlines, scheduler, due
    ):
        deadlines.set(KIND, KEY, 10)
        deadlines.set(KIND, KEY, 20)

        await scheduler.run_cancelled()

        assert due == []

    async def test_a_kind_registered_twice_is_refused(self, deadlines):
        with pytest.raises(ValueError, match="already has a handler"):
            deadlines.register(KIND, lambda key: None)

    async def test_the_same_key_of_another_kind_is_another_deadline(self, deadlines, scheduler, due):
        async def other(key):
            due.append((OTHER_KIND, key))

        deadlines.register(OTHER_KIND, other)
        deadlines.set(KIND, KEY, 10)
        deadlines.set(OTHER_KIND, KEY, 10)
        deadlines.cancel(KIND, KEY)

        await scheduler.advance(10)

        assert due == [(OTHER_KIND, KEY)]
