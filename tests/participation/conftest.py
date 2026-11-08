import asyncio
from functools import partial

import pytest
import pytest_asyncio
from agent_runtime_client.wait import LEASE_SECONDS

from hopin.participation.feature import Participation
from hopin.signaling.handler import SignalingHandler
from tests.participation.support.own_accounts import WALL_CLOCK_SECONDS
from tests.signaling.support.browsers import Link
from tests.support.signaling_fakes import SocketAccountResolver


@pytest.fixture
def changes():
    """The persons `on_changed` named, in order."""
    return []


@pytest.fixture
def adders():
    """The owner's subject mapped to the group that may add the owner's agent; a module that adds overrides it."""
    return {}


@pytest.fixture
def groups_of_seat():
    """The groups the issuer holds for the seat of each name; `None` is a read that fails and an `asyncio.Event` a
    read that waits for it and then finds the adders group. A seat never named holds none."""
    return {}


@pytest.fixture
def group_reads(groups_of_seat):
    """How many times a seat's groups were read."""
    return []


@pytest.fixture
def handler(room_manager, scheduler, changes, adders, groups_of_seat, group_reads):
    async def groups_of(connection):
        group_reads.append(connection.account.name)
        groups = groups_of_seat.get(connection.account.name, frozenset())
        if isinstance(groups, asyncio.Event):
            await groups.wait()
            return frozenset(adders.values())
        return groups

    return SignalingHandler(
        room_manager,
        SocketAccountResolver(),
        scheduler,
        [
            partial(
                Participation,
                scheduler=scheduler,
                lease_seconds=LEASE_SECONDS,
                on_changed=changes.append,
                wall_clock=lambda: WALL_CLOCK_SECONDS,
                adders=adders,
                groups_of=groups_of,
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
