from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest

from hopin.auth.sign_in import build_sign_in_configs
from hopin.config.constants import ACCOUNT_PATH, HEALTH_PATH, LOGOUT_PATH
from hopin.config.settings import ServerSettings
from tests.stores.support.pod_pair import starting_pod
from tests.support.in_thread_stores import in_thread_stores
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN, TEST_REALM
from tests.support.signed_in import cookie_header, seed_member

if TYPE_CHECKING:
    from pathlib import Path

    from webauth.config import WebAuthConfig

    from tests.stores.support.postgres_identity import PostgresStores

pytestmark = pytest.mark.shared_stores

SAME_ORIGIN_POST = {"sec-fetch-site": "same-origin", "origin": TEST_PUBLIC_ORIGIN}


@pytest.fixture
def signed_in_cookie(pods, postgres_stores: PostgresStores) -> dict[str, str]:
    """The cookie of a session written to the database, which no process has seen."""
    config: WebAuthConfig = build_sign_in_configs(
        ServerSettings(), TEST_REALM, (), in_thread_stores().rate_limits
    ).web_auth
    return {"cookie": cookie_header(seed_member(postgres_stores, config).set_cookie_headers)}


class TestIdentityInTwoProcesses:
    def test_both_processes_admit_a_session_neither_wrote(self, pods, signed_in_cookie):
        statuses = [pod.status(ACCOUNT_PATH, headers=signed_in_cookie) for pod in pods]

        assert statuses == [HTTPStatus.OK, HTTPStatus.OK]

    def test_a_process_started_after_the_sign_in_admits_the_session(
        self, pods, signed_in_cookie, served_environment, redis_url, tmp_path: Path
    ):
        with starting_pod(served_environment, redis_url, tmp_path) as restarted:
            restarted.wait_until_serving(HEALTH_PATH)

            assert restarted.status(ACCOUNT_PATH, headers=signed_in_cookie) == HTTPStatus.OK

    def test_a_logout_through_one_process_is_refused_by_the_other_and_by_one_started_after_it(
        self, pods, signed_in_cookie, served_environment, redis_url, tmp_path: Path
    ):
        first, second = pods
        assert second.status(ACCOUNT_PATH, headers=signed_in_cookie) == HTTPStatus.OK

        assert (
            first.status(LOGOUT_PATH, method="POST", headers={**signed_in_cookie, **SAME_ORIGIN_POST}) == HTTPStatus.OK
        )

        with starting_pod(served_environment, redis_url, tmp_path) as restarted:
            restarted.wait_until_serving(HEALTH_PATH)
            assert [pod.status(ACCOUNT_PATH, headers=signed_in_cookie) for pod in (second, restarted)] == [
                HTTPStatus.UNAUTHORIZED
            ] * 2
