from functools import partial

import pytest
import pytest_asyncio

from hopin.signaling.handler import SignalingHandler
from hopin.skat.feature import Skat
from tests.signaling.support.browsers import Link
from tests.support.signaling_fakes import SocketAccountResolver

ORIGIN = "https://skat.example"
TABLE_ID = "0123456789abcdef0123456789abcdef"
TABLE_LINK = f"{ORIGIN}/t/{TABLE_ID}"
OTHER_TABLE_LINK = f"{ORIGIN}/t/{TABLE_ID[::-1]}"


@pytest.fixture
def handler(room_manager, scheduler):
    return SignalingHandler(room_manager, SocketAccountResolver(), scheduler, [partial(Skat, origin=ORIGIN)])


@pytest_asyncio.fixture
async def link(handler):
    link = Link(handler)
    yield link
    await link.close_all()
