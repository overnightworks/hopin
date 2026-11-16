import socket
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from hopin.app import create_app
from hopin.config.constants import LOGIN_PATH
from hopin.stores.redis import connect_shared_stores
from tests.support.page_build import fixture_page_build
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN

pytestmark = pytest.mark.integration


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
        page_build=fixture_page_build(),
        shared_stores=connect_shared_stores(unreachable_redis_url),
    )
    return TestClient(app, base_url=TEST_PUBLIC_ORIGIN, raise_server_exceptions=False)


class TestRedisDown:
    def test_a_budgeted_route_answers_503_with_a_retry_hint(self, served_without_redis):
        refused = served_without_redis.get(LOGIN_PATH)

        assert refused.status_code == HTTPStatus.SERVICE_UNAVAILABLE
        assert "retry-after" in refused.headers
