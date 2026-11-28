import json
import socket
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from logline.testing import configured

from hopin.agent.mcp import PLACE_SUBSCRIPTION_TIMED_OUT_EVENT, ParticipationFeature
from hopin.app import create_app
from hopin.config.constants import HEALTH_PATH, LOGIN_PATH
from hopin.stores.redis import connect_shared_stores
from tests.support.in_thread_stores import in_thread_stores
from tests.support.page_build import fixture_page_build
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN, TEST_REALM
from tests.support.stores import single_process_stores

pytestmark = pytest.mark.integration

SHORT_SUBSCRIPTION_WAIT_SECONDS = 0.2
SHORT_SUBSCRIPTION_WAIT_MS = 200


@pytest.fixture
def unreachable_redis_url() -> str:
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
    return f"redis://127.0.0.1:{port}"


@pytest.fixture
def served_without_redis(sign_in_settings, unreachable_redis_url) -> TestClient:
    app = create_app(
        sign_in_settings().settings,
        TEST_REALM,
        page_build=fixture_page_build(),
        shared_stores=connect_shared_stores(unreachable_redis_url),
        identity=single_process_stores(),
    )
    return TestClient(app, base_url=TEST_PUBLIC_ORIGIN, raise_server_exceptions=False)


@pytest.fixture
def started_without_subscription(sign_in_settings, unreachable_redis_url) -> TestClient:
    """A start whose agents' place subscription never comes up while the call state is served, entered as the
    `TestClient` it returns."""
    settings = sign_in_settings().settings
    app = create_app(
        settings,
        TEST_REALM,
        page_build=fixture_page_build(),
        shared_stores=in_thread_stores(),
        call_features=(
            ParticipationFeature(
                settings.public_origin,
                redis=connect_shared_stores(unreachable_redis_url).async_redis,
                realm=TEST_REALM,
                subscription_wait_seconds=SHORT_SUBSCRIPTION_WAIT_SECONDS,
            ),
        ),
        identity=single_process_stores(),
    )
    return TestClient(app, base_url=TEST_PUBLIC_ORIGIN)


class TestRedisDown:
    def test_a_budgeted_route_answers_503_with_a_retry_hint(self, served_without_redis):
        refused = served_without_redis.get(LOGIN_PATH)

        assert refused.status_code == HTTPStatus.SERVICE_UNAVAILABLE
        assert "retry-after" in refused.headers

    def test_health_answers_503(self, served_without_redis):
        assert served_without_redis.get(HEALTH_PATH).status_code == HTTPStatus.SERVICE_UNAVAILABLE

    def test_a_start_whose_place_subscription_never_comes_up_fails_and_logs_its_wait_once_through_the_real_logger(
        self, started_without_subscription, capsys
    ):
        with configured("hopin"), pytest.raises(TimeoutError), started_without_subscription:
            pass

        lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
        (timed_out,) = [line for line in lines if line["event"] == PLACE_SUBSCRIPTION_TIMED_OUT_EVENT]
        assert timed_out["wait_ms"] == SHORT_SUBSCRIPTION_WAIT_MS
