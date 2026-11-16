import socket
import time
from http import HTTPStatus
from urllib.parse import urlsplit

import pytest
from agents.member import LEASE_SECONDS, STANDING_URI
from redis import Redis

from hopin.config.constants import HEALTH_PATH
from hopin.stores.redis import connect_shared_stores
from tests.stores.support.pod_pair import LOCALHOST, starting_pod
from tests.stores.support.redis_gate import RedisGate
from tests.support.dialed_call import RESOURCE_UPDATED, serving_dialed_call
from tests.support.fake_issuer import FakeIssuer
from tests.support.log_lines import captured_lines

pytestmark = pytest.mark.shared_stores

SIGNAL_WAIT_SECONDS = 10
NOT_READY_OBSERVED_SECONDS = 3
PLACE_SIGNAL_FAILED = "place_signal.failed"


@pytest.fixture
def agent_call(sign_in_settings, redis_gate):
    """HopIn in this process over the real Redis, and the token of an agent that has called once, so that it holds a
    lease. A call of the same agent for another owner, and the end of the lease, change its standing."""
    sign_in = sign_in_settings()
    with serving_dialed_call(sign_in, FakeIssuer(sign_in), shared_stores=connect_shared_stores(redis_gate.url)) as call:
        token = call.agent_token(owner="owner-subject", subject="agent-subject")
        call.standing(token)
        yield call, token


def kill_subscriptions(redis_url: str) -> int:
    """Close every pub/sub connection to this test's database, which is the subscription going down while Redis stays
    up."""
    database = int(urlsplit(redis_url).path.removeprefix("/"))
    with Redis.from_url(redis_url) as admin:
        subscriptions = [client for client in admin.client_list(_type="pubsub") if int(client["db"]) == database]
        for client in subscriptions:
            admin.client_kill_filter(_id=client["id"])
    return len(subscriptions)


class TestAgentPlaceSignals:
    def test_a_change_wakes_a_listen_open_on_the_same_process(self, agent_call):
        call, token = agent_call

        with call.open_listen(token, [STANDING_URI]) as stream:
            call.standing(call.agent_token(owner="another-owner-subject", subject="agent-subject"))

            assert stream.wait_for(RESOURCE_UPDATED, SIGNAL_WAIT_SECONDS)

    def test_a_listen_whose_subscription_was_killed_gets_the_resync_pulse(self, agent_call, redis_url):
        call, token = agent_call

        with call.open_listen(token, [STANDING_URI]) as stream:
            assert kill_subscriptions(redis_url) == 1

            assert stream.wait_for(RESOURCE_UPDATED, SIGNAL_WAIT_SECONDS)

    def test_while_redis_refuses_the_closed_code_is_logged_once(self, agent_call, redis_gate):
        call, _token = agent_call

        with captured_lines() as lines:
            redis_gate.close()
            call.pass_time(LEASE_SECONDS)
            assert lines.wait_for_event(PLACE_SIGNAL_FAILED, SIGNAL_WAIT_SECONDS)

        assert [line["event"] for line in lines].count(PLACE_SIGNAL_FAILED) == 1


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind((LOCALHOST, 0))
        return probe.getsockname()[1]


class TestReadinessWaitsForTheSubscription:
    def test_a_pod_answers_no_health_until_its_subscription_is_up(self, sign_in_settings, redis_url, tmp_path):
        port = free_port()
        unreachable = f"redis://{LOCALHOST}:{port}{urlsplit(redis_url).path}"
        with starting_pod(sign_in_settings().environment, unreachable, tmp_path) as pod:
            deadline = time.monotonic() + NOT_READY_OBSERVED_SECONDS
            while time.monotonic() < deadline:
                with pytest.raises(OSError, match=r"."):
                    pod.status(HEALTH_PATH)
                time.sleep(0.2)
            reachable = RedisGate(redis_url, port)
            try:
                pod.wait_until_serving(HEALTH_PATH)
                assert pod.status(HEALTH_PATH) == HTTPStatus.OK
            finally:
                reachable.close()
