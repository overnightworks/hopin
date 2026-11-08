import asyncio

import pytest

from hopin.participation.feature import GROUPS_READ_TASK_NAME
from tests.participation.support.own_accounts import let_reads_finish


@pytest.mark.asyncio
async def test_a_failed_read_fails_the_wait_for_the_reads():
    async def failing_read():
        raise RuntimeError("the read failed")

    task = asyncio.create_task(failing_read(), name=GROUPS_READ_TASK_NAME)

    with pytest.raises(RuntimeError, match="the read failed"):
        await let_reads_finish()
    assert task.done()
