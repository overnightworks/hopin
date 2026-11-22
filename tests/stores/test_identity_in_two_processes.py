import json
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from webauth.admission import AppRole
from webauth.ports import AccountKind

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

    from tests.stores.support.pod_pair import Pod
    from tests.stores.support.postgres_identity import PostgresStores
    from tests.support.signed_in import SeededMember

pytestmark = pytest.mark.shared_stores

SAME_ORIGIN_POST = {"sec-fetch-site": "same-origin", "origin": TEST_PUBLIC_ORIGIN}


@pytest.fixture
def web_auth_config() -> WebAuthConfig:
    return build_sign_in_configs(ServerSettings(), TEST_REALM, (), in_thread_stores().rate_limits).web_auth


@pytest.fixture
def member(pods, postgres_stores: PostgresStores, web_auth_config: WebAuthConfig) -> SeededMember:
    """A person whose session is written to the database, which no process has seen."""
    return seed_member(postgres_stores, web_auth_config)


@pytest.fixture
def signed_in_cookie(member: SeededMember) -> dict[str, str]:
    return {"cookie": cookie_header(member.set_cookie_headers)}


@pytest.fixture
def admin_headers(postgres_stores: PostgresStores, web_auth_config: WebAuthConfig) -> dict[str, str]:
    """The headers of an admin's page asking to change something."""
    account = postgres_stores.accounts.create_for_subject("olga", "olga", AppRole.ADMIN, AccountKind.PERSON)
    cookies = seed_member(postgres_stores, web_auth_config, account=account).set_cookie_headers
    return {"cookie": cookie_header(cookies)} | SAME_ORIGIN_POST


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


class TestErasureInTwoProcesses:
    @staticmethod
    def erased_through(pod: Pod, subject: str, headers: dict[str, str], *, dry_run: bool = False) -> tuple[int, dict]:
        query = "?dry_run=true" if dry_run else ""
        status, body = pod.answer(f"/api/admin/people/{subject}{query}", method="DELETE", headers=headers)
        return status, json.loads(body)

    @staticmethod
    def counts(answer: dict) -> tuple[list[int], list[int]]:
        return [kind["count"] for kind in answer["kinds"]], [effect["count"] for effect in answer["affects_others"]]

    def test_an_erasure_through_one_process_is_refused_by_the_other_which_then_answers_zeros(
        self, pods, member, signed_in_cookie, admin_headers
    ):
        first, second = pods
        assert second.status(ACCOUNT_PATH, headers=signed_in_cookie) == HTTPStatus.OK
        subject = member.account.id

        status, erased = self.erased_through(first, subject, admin_headers)

        assert (status, self.counts(erased)) == (HTTPStatus.OK, ([1, 1, 0], [0]))
        assert second.status(ACCOUNT_PATH, headers={**signed_in_cookie, "accept": "application/json"}) == (
            HTTPStatus.UNAUTHORIZED
        )
        assert second.status(ACCOUNT_PATH, headers={**signed_in_cookie, "accept": "text/html"}) == HTTPStatus.FOUND
        assert self.counts(self.erased_through(second, subject, admin_headers)[1]) == ([0, 0, 0], [0])

    def test_a_dry_run_answers_while_a_sign_in_entry_holds_the_lock_and_deletes_nothing(
        self, pods, member, signed_in_cookie, admin_headers, postgres_stores: PostgresStores
    ):
        first, second = pods

        with postgres_stores.sign_in_transaction():
            status, counted = self.erased_through(first, member.account.id, admin_headers, dry_run=True)

        assert (status, counted["dry_run"], self.counts(counted)) == (HTTPStatus.OK, True, ([1, 1, 0], [0]))
        assert second.status(ACCOUNT_PATH, headers=signed_in_cookie) == HTTPStatus.OK
