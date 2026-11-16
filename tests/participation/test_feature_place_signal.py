"""A change to an agent's places stands while the store refuses its signal, and the refusal is logged once."""

import pytest
from agents.member import LEASE_SECONDS
from fakeredis import FakeAsyncRedis, FakeServer
from webauth.rate_limit import SingleProcessRateLimitBackend

from hopin.agent.mcp import PLACE_SIGNAL_FAILED_EVENT
from hopin.stores.shared import SharedStores
from tests.support.log_lines import captured_lines

pytestmark = pytest.mark.integration

SEEN_WITHIN_SECONDS = 10


@pytest.fixture
def redis_server():
    """The in-memory Redis the call's place store runs on; a test takes it down by setting `connected` false."""
    return FakeServer()


@pytest.fixture
def shared_stores(redis_server):
    return SharedStores(
        rate_limits=SingleProcessRateLimitBackend(),
        answers=lambda: True,
        async_redis=FakeAsyncRedis(server=redis_server),
    )


class TestAPlaceSignalTheStoreRefuses:
    def test_the_change_stands_and_the_refusal_is_logged_once(self, scene, redis_server):
        scene.invite()
        [standing] = scene.standings()
        scene.accept(standing)
        assert len(scene.call.places_held_by(scene.agent_subject)) == 1

        with captured_lines() as lines:
            redis_server.connected = False
            scene.call.pass_time(LEASE_SECONDS)
            assert lines.wait_for_event(PLACE_SIGNAL_FAILED_EVENT, SEEN_WITHIN_SECONDS)

        assert (
            scene.call.places_held_by(scene.agent_subject),
            [line["event"] for line in lines].count(PLACE_SIGNAL_FAILED_EVENT),
        ) == ((), 1)
