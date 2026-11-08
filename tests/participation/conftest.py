from functools import partial

import pytest
import pytest_asyncio
from agent_runtime_client.wait import LEASE_SECONDS

from hopin.participation.feature import Participation
from hopin.signaling.handler import SignalingHandler
from tests.participation.support.ports import unconfigured_ports
from tests.signaling.support.browsers import Link
from tests.support.signaling_fakes import SocketAccountResolver


@pytest.fixture
def handler(room_manager, scheduler):
    return SignalingHandler(
        room_manager,
        SocketAccountResolver(),
        scheduler,
        [
            partial(
                Participation,
                scheduler=scheduler,
                lease_seconds=LEASE_SECONDS,
                on_changed=lambda person: None,
                **unconfigured_ports(),
            )
        ],
    )


@pytest.fixture
def participation(handler) -> Participation:
    [feature] = handler.features
    return feature


@pytest_asyncio.fixture
async def link(handler):
    link = Link(handler)
    yield link
    await link.close_all()
