"""What the offers make of the store's reads of who listens: a refused read offers no agent and is logged once, and an
older read that ends after a newer one does not undo it."""

import asyncio
import contextlib
import threading

import pytest
from agents.member import STANDING_URI
from redis.exceptions import ConnectionError as RedisConnectionError

from hopin.agent.mcp import LISTENERS_READ_FAILED_EVENT
from hopin.participation.memberships import AGENT_ADDERS_GROUP
from hopin.participation.vocabulary import ParticipationSignal
from tests.support.log_lines import captured_lines

pytestmark = pytest.mark.integration

# The last argument of agents' script that reads every listener of a place rather than one principal's.
ALL_LISTENERS = -1
HELD_AT_MOST_SECONDS = 30


class ListenersReadRefusal:
    """The in-memory Redis answers every script but the read of all listeners once `begin` has been called."""

    def __init__(self, redis) -> None:
        self._evalsha = redis.evalsha
        self._refusing = False
        redis.evalsha = self._run

    def begin(self) -> None:
        self._refusing = True

    async def _run(self, *arguments, **options):
        if self._refusing and arguments[-1] == ALL_LISTENERS:
            raise RedisConnectionError("refused")
        return await self._evalsha(*arguments, **options)


class FirstEmptyListenersReadHeld:
    """The in-memory Redis holds back the answer of the first read of all listeners that finds none, until `release`
    or for at most `HELD_AT_MOST_SECONDS`; `returned` is set as that answer goes back."""

    def __init__(self, redis) -> None:
        self._evalsha = redis.evalsha
        self._held = False
        self._released: asyncio.Event | None = None
        self.returned = threading.Event()
        redis.evalsha = self._run

    def release(self, portal) -> None:
        portal.call(self._releasing)

    async def _releasing(self) -> None:
        self._release_event().set()

    def _release_event(self) -> asyncio.Event:
        if self._released is None:
            self._released = asyncio.Event()
        return self._released

    async def _run(self, *arguments, **options):
        answer = await self._evalsha(*arguments, **options)
        if arguments[-1] == ALL_LISTENERS and not answer and not self._held:
            self._held = True
            # Bounded, so that a test that fails before its release still lets the app shut down.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._release_event().wait(), HELD_AT_MOST_SECONDS)
            self.returned.set()
        return answer


@pytest.fixture
def refusal(shared_stores):
    return ListenersReadRefusal(shared_stores.async_redis)


@pytest.fixture
def held_read(shared_stores):
    """Wraps the store before the app starts, so that the read the start makes is the one held."""
    return FirstEmptyListenersReadHeld(shared_stores.async_redis)


SEEN_WITHIN_SECONDS = 10


def offers_to_a_new_seat(scene):
    _, login = scene.call.member(groups=(AGENT_ADDERS_GROUP,))
    return scene.call.seat(login, scene.owner.room_id).frame_of_type(ParticipationSignal.AGENT_OFFERS.value)["offers"]


class TestAFailedListenersRead:
    def test_offers_no_agent_and_logs_the_event_once(self, scene, refusal):
        scene.offered()
        refusal.begin()

        with captured_lines() as lines:
            scene.call.listen(scene.token, [STANDING_URI])
            assert lines.wait_for_event(LISTENERS_READ_FAILED_EVENT, SEEN_WITHIN_SECONDS)
            offers = offers_to_a_new_seat(scene)

        assert (offers, [line["event"] for line in lines].count(LISTENERS_READ_FAILED_EVENT)) == ([], 1)


class TestAnOlderListenersReadThatEndsLast:
    def test_leaves_the_agent_the_newer_read_found_offered(self, held_read, scene):
        assert [offer["name"] for offer in scene.offered()] == [scene.agent_name]

        held_read.release(scene.call.client.portal)
        assert held_read.returned.wait(SEEN_WITHIN_SECONDS)

        assert [offer["name"] for offer in offers_to_a_new_seat(scene)] == [scene.agent_name]
