import pytest
import pytest_asyncio

from hopin.config.constants import ErrorReason, SignalType
from tests.signaling.support.browsers import Browser, Link
from tests.support.signaling_fakes import ROOM_LIMIT, account_named, text_frame

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

EXTRA_GUESTS = 3


@pytest_asyncio.fixture
async def link(handler):
    link = Link(handler)
    yield link
    await link.close_all()


async def connect(link, name):
    browser = Browser(link, name)
    browser.sockets.append(await link.open(account_named(name)))
    return browser


class TestSeatCap:
    async def test_concurrent_joins_seat_exactly_the_room_limit_and_refuse_the_rest_as_full(self, link, room_manager):
        host = await link.join("host1")
        room_id = host.socket.sent[0]["room_id"]
        guests = [await connect(link, f"guest{index}") for index in range(ROOM_LIMIT + EXTRA_GUESTS)]

        for guest in guests:
            guest.socket.push(text_frame({"type": SignalType.JOIN.value, "room_id": room_id}))
        for guest in guests:
            await link.settle(guest.socket)

        assert len(room_manager.get_room(room_id).participants) == ROOM_LIMIT
        refusals = [frame for guest in guests for frame in guest.frames(SignalType.ERROR)]
        assert [frame["reason"] for frame in refusals] == [ErrorReason.FULL.value] * (EXTRA_GUESTS + 1)
