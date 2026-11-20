import pytest
import pytest_asyncio

from hopin.signaling.handler import SignalingHandler
from hopin.stage.feature import StageTransitions
from tests.signaling.support.browsers import Link
from tests.stage.support.tables import ORIGIN
from tests.support.signaling_fakes import SocketAccountResolver


@pytest.fixture
def handler(room_manager, scheduler, deadlines):
    return SignalingHandler(room_manager, SocketAccountResolver(), scheduler, deadlines, [StageTransitions(ORIGIN)])


@pytest_asyncio.fixture
async def link(handler):
    link = Link(handler)
    yield link
    await link.close_all()
