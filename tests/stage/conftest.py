import pytest
import pytest_asyncio

from hopin.signaling.handler import SignalingHandler
from hopin.stage.feature import StageTransitions
from tests.signaling.support.browsers import Link
from tests.stage.support.tables import SKAT
from tests.support.signaling_fakes import SocketAccountResolver


@pytest.fixture
def stage():
    return StageTransitions([SKAT])


@pytest.fixture
def handler(call_store, scheduler, deadlines, stage):
    return SignalingHandler(call_store, SocketAccountResolver(), scheduler, deadlines, [stage])


@pytest_asyncio.fixture
async def link(handler):
    link = Link(handler)
    yield link
    await link.close_all()
