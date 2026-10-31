import pytest
from fastapi.testclient import TestClient
from webauth.config import installed_web_auth_config

from hopin.app import create_app
from hopin.auth.stores import SignInStores
from hopin.config.constants import ACCOUNT_CONSOLE_PATH, ACCOUNT_PATH
from tests.support.page_build import fixture_page_build
from tests.support.sign_in_settings import TEST_ISSUER, TEST_PUBLIC_ORIGIN
from tests.support.signed_in import cookie_header, seed_member

pytestmark = pytest.mark.integration


@pytest.fixture
def stores():
    return SignInStores.in_memory()


@pytest.fixture
def app(sign_in_settings, stores):
    return create_app(sign_in_settings().settings, page_build=fixture_page_build(), stores=stores)


@pytest.fixture
def browser(app):
    return TestClient(app, base_url=TEST_PUBLIC_ORIGIN)


class TestAccountRoute:
    def test_answers_a_signed_in_member_with_exactly_their_username(self, app, stores, browser):
        member = seed_member(stores, installed_web_auth_config(app))

        answer = browser.get(ACCOUNT_PATH, headers={"cookie": cookie_header(member.set_cookie_headers)})

        assert (answer.status_code, answer.json()) == (200, {"name": member.account.username})

    @pytest.mark.parametrize("session", ["absent", "ended"])
    def test_refuses_a_request_without_a_live_session_and_names_nobody(self, app, stores, browser, session):
        member = seed_member(stores, installed_web_auth_config(app))
        headers = {"accept": "application/json"}
        if session == "ended":
            stores.sessions.delete(member.session.id)
            headers["cookie"] = cookie_header(member.set_cookie_headers)

        answer = browser.get(ACCOUNT_PATH, headers=headers)

        assert answer.status_code == 401
        assert member.account.username not in answer.text


class TestAccountConsoleRoute:
    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    def test_sends_a_signed_in_member_to_the_issuers_account_console(self, app, stores, browser, method):
        member = seed_member(stores, installed_web_auth_config(app))

        answer = browser.request(
            method,
            ACCOUNT_CONSOLE_PATH,
            params={"next": "https://example.org"},
            headers={"cookie": cookie_header(member.set_cookie_headers)},
            follow_redirects=False,
        )

        assert (answer.status_code, answer.headers["location"]) == (303, f"{TEST_ISSUER}/account")
