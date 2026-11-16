"""A store that refuses the read of who listens offers no agent, and the refusal is logged once."""

import pytest
from agents.member import STANDING_URI
from redis.exceptions import ConnectionError as RedisConnectionError

from hopin.participation.vocabulary import ParticipationSignal
from tests.support.agent_adders import ADDERS_GROUP
from tests.support.log_lines import captured_lines

pytestmark = pytest.mark.integration

LISTENERS_READ_FAILED = "listeners_read.failed"
# The last argument of agents' script that reads every listener of a place rather than one principal's.
ALL_LISTENERS = -1


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


@pytest.fixture
def refusal(shared_stores):
    return ListenersReadRefusal(shared_stores.async_redis)


SEEN_WITHIN_SECONDS = 10


class TestAFailedListenersRead:
    def test_offers_no_agent_and_logs_the_event_once(self, scene, refusal):
        scene.offered()
        refusal.begin()

        with captured_lines() as lines:
            scene.call.listen(scene.token, [STANDING_URI])
            assert lines.wait_for_event(LISTENERS_READ_FAILED, SEEN_WITHIN_SECONDS)
            _, login = scene.call.member(groups=(ADDERS_GROUP,))
            offers = scene.call.seat(login, scene.owner.room_id).frame_of_type(ParticipationSignal.AGENT_OFFERS.value)[
                "offers"
            ]

        assert (offers, [line["event"] for line in lines].count(LISTENERS_READ_FAILED)) == ([], 1)
