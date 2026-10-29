import types
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
import webauth.rate_limit
from fastapi.testclient import TestClient

from hopin.app import create_app
from hopin.auth.sign_in import SignInConfigurationError, build_sign_in_configs
from hopin.config.constants import (
    ACCOUNT_PATH,
    AUTH_CALLBACK_PATH,
    AUTH_START_PATH,
    BACKCHANNEL_LOGOUT_PATH,
    HEALTH_PATH,
    LOGIN_PATH,
    LOGOUT_PATH,
    NO_ACCESS_PATH,
)
from tests.support.fake_issuer import FakeIssuer
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN

pytestmark = pytest.mark.integration

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.support.sign_in_settings import SignInSettings

SIGN_IN_BUDGET_REQUESTS = 40
SIGN_IN_WINDOW_SECONDS = 600
DEFAULT_BUDGET_REQUESTS = 600
DEFAULT_WINDOW_SECONDS = 60
COMPLETE_SIGN_INS_IN_WINDOW = SIGN_IN_BUDGET_REQUESTS // 2
PROXY_ADDRESS = "172.31.250.1"
TRUSTED_PROXIES = f"{PROXY_ADDRESS},127.0.0.1"
FIRST_ADDRESS = "203.0.113.5"
OTHER_ADDRESS = "203.0.113.6"
BROWSER_ACCEPT = {"accept": "text/html"}
FORM_POST = {"content-type": "application/x-www-form-urlencoded"}


class LimiterClock:
    """Stands in for the `time` binding of the webauth limiter, the only clock it reads."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class Deployment:
    app: object
    issuer: FakeIssuer
    clock: LimiterClock

    def client(self, address: str = FIRST_ADDRESS, *, headers: dict[str, str] | None = None) -> TestClient:
        return TestClient(
            self.app,
            base_url=TEST_PUBLIC_ORIGIN,
            client=(address, 50000),
            follow_redirects=False,
            raise_server_exceptions=False,
            headers=headers,
        )

    def codes(self, count: int) -> list[str]:
        """Callback queries carrying a valid code, from a start made by an address nobody else uses."""
        start = self.client("192.0.2.200").get(AUTH_START_PATH)
        return [
            self.issuer.authorize(start.headers["location"], subject="anna", groups=["/apps/hopin"], username="anna")
            for _ in range(count)
        ]

    def complete_sign_in(self, address: str) -> int:
        browser = self.client(address)
        start = browser.get(AUTH_START_PATH)
        assert start.status_code == HTTPStatus.SEE_OTHER
        query = self.issuer.authorize(
            start.headers["location"], subject="anna", groups=["/apps/hopin"], username="anna"
        )
        callback = browser.get(f"{AUTH_CALLBACK_PATH}?{query}")
        assert callback.headers["location"] != NO_ACCESS_PATH
        account = browser.get(ACCOUNT_PATH)
        assert (account.status_code, account.json()) == (HTTPStatus.OK, {"name": "anna"})
        return callback.status_code

    def post_malformed_logout_token(self, browser: TestClient, path: str = BACKCHANNEL_LOGOUT_PATH) -> int:
        return browser.post(path, content="logout_token=not-a-token", headers=FORM_POST).status_code


@pytest.fixture
def make_deployment(sign_in_settings: Callable[..., SignInSettings], monkeypatch) -> Callable[..., Deployment]:
    clock = LimiterClock()
    monkeypatch.setattr(webauth.rate_limit, "time", types.SimpleNamespace(monotonic=clock.monotonic))

    def make(**overrides: str) -> Deployment:
        sign_in = sign_in_settings(**overrides)
        issuer = FakeIssuer(sign_in)
        return Deployment(create_app(sign_in.settings, issuer_transports=issuer.transports), issuer, clock)

    return make


@pytest.fixture
def deployment(make_deployment) -> Deployment:
    return make_deployment()


@pytest.fixture
def exhausted_sign_in(deployment) -> Deployment:
    for _ in range(COMPLETE_SIGN_INS_IN_WINDOW):
        assert deployment.complete_sign_in(FIRST_ADDRESS) == HTTPStatus.SEE_OTHER
    return deployment


@pytest.fixture
def exhausted_default(deployment) -> Deployment:
    browser = deployment.client()
    for _ in range(DEFAULT_BUDGET_REQUESTS):
        assert browser.get(LOGIN_PATH).status_code == HTTPStatus.OK
    return deployment


class TestSignInBudget:
    def test_twenty_complete_sign_ins_from_one_address_each_open_a_session(self, deployment):
        for _ in range(COMPLETE_SIGN_INS_IN_WINDOW):
            assert deployment.complete_sign_in(FIRST_ADDRESS) == HTTPStatus.SEE_OTHER

    def test_a_start_and_a_callback_after_the_budget_is_spent_are_refused_with_the_window(self, exhausted_sign_in):
        browser = exhausted_sign_in.client()
        (code_query,) = exhausted_sign_in.codes(1)

        for response in (browser.get(AUTH_START_PATH), browser.get(f"{AUTH_CALLBACK_PATH}?{code_query}")):
            assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
            assert response.headers["retry-after"] == str(SIGN_IN_WINDOW_SECONDS)

    def test_another_address_still_starts_after_the_budget_is_spent(self, exhausted_sign_in):
        assert exhausted_sign_in.client(OTHER_ADDRESS).get(AUTH_START_PATH).status_code == HTTPStatus.SEE_OTHER

    def test_the_sign_in_budget_returns_when_its_window_has_passed(self, exhausted_sign_in):
        exhausted_sign_in.clock.advance(SIGN_IN_WINDOW_SECONDS)
        assert exhausted_sign_in.client().get(AUTH_START_PATH).status_code == HTTPStatus.SEE_OTHER

    def test_a_logout_spends_the_sign_in_budget_too(self, deployment):
        browser = deployment.client()
        for _ in range(SIGN_IN_BUDGET_REQUESTS):
            assert browser.get(AUTH_START_PATH).status_code == HTTPStatus.SEE_OTHER

        refused = browser.post(LOGOUT_PATH, headers={**FORM_POST, "origin": TEST_PUBLIC_ORIGIN})

        assert refused.status_code == HTTPStatus.TOO_MANY_REQUESTS
        assert refused.headers["retry-after"] == str(SIGN_IN_WINDOW_SECONDS)

    def test_starts_and_callbacks_spend_one_shared_budget_in_either_order(self, deployment):
        starter = deployment.client("198.51.100.20")
        for _ in range(SIGN_IN_BUDGET_REQUESTS):
            assert starter.get(AUTH_START_PATH).status_code == HTTPStatus.SEE_OTHER
        (code_query,) = deployment.codes(1)
        assert starter.get(f"{AUTH_CALLBACK_PATH}?{code_query}").status_code == HTTPStatus.TOO_MANY_REQUESTS

        caller = deployment.client("198.51.100.21")
        for query in deployment.codes(SIGN_IN_BUDGET_REQUESTS // 2):
            assert caller.get(f"{AUTH_CALLBACK_PATH}?{query}").status_code != HTTPStatus.TOO_MANY_REQUESTS
        for _ in range(SIGN_IN_BUDGET_REQUESTS // 2):
            assert caller.get(AUTH_START_PATH).status_code == HTTPStatus.SEE_OTHER
        assert caller.get(AUTH_START_PATH).status_code == HTTPStatus.TOO_MANY_REQUESTS


class TestExemptions:
    def test_the_back_channel_the_health_check_and_the_home_page_answer_after_the_sign_in_budget_is_spent(
        self, exhausted_sign_in
    ):
        browser = exhausted_sign_in.client()
        assert exhausted_sign_in.post_malformed_logout_token(browser) == HTTPStatus.BAD_REQUEST
        assert browser.get(HEALTH_PATH).status_code == HTTPStatus.OK
        assert browser.get("/", headers=BROWSER_ACCEPT).status_code == HTTPStatus.FOUND

    def test_the_exempt_paths_answer_after_the_default_budget_is_spent(self, exhausted_default):
        browser = exhausted_default.client()
        assert browser.get(LOGIN_PATH).status_code == HTTPStatus.TOO_MANY_REQUESTS
        assert browser.get(AUTH_START_PATH).status_code == HTTPStatus.SEE_OTHER
        assert browser.get(HEALTH_PATH).status_code == HTTPStatus.OK
        assert exhausted_default.post_malformed_logout_token(browser) != HTTPStatus.TOO_MANY_REQUESTS

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("POST", f"{BACKCHANNEL_LOGOUT_PATH}/"),
            ("POST", f"{BACKCHANNEL_LOGOUT_PATH}x"),
            ("GET", f"{HEALTH_PATH}/"),
            ("GET", f"{HEALTH_PATH}x"),
        ],
    )
    def test_a_lookalike_of_an_exempt_path_spends_the_default_budget(self, exhausted_default, method, path):
        response = exhausted_default.client().request(method, path, headers=FORM_POST)
        assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS


class TestDefaultBudget:
    def test_the_601st_request_in_a_minute_is_refused_and_the_next_minute_is_open(self, exhausted_default):
        browser = exhausted_default.client()
        refused = browser.get(LOGIN_PATH)
        assert refused.status_code == HTTPStatus.TOO_MANY_REQUESTS
        assert refused.headers["retry-after"] == str(DEFAULT_WINDOW_SECONDS)

        exhausted_default.clock.advance(DEFAULT_WINDOW_SECONDS)
        assert browser.get(LOGIN_PATH).status_code == HTTPStatus.OK


class TestAddressResolution:
    def exhaust_starts(self, browser_for: Callable[[int], TestClient]) -> list[int]:
        return [browser_for(index).get(AUTH_START_PATH).status_code for index in range(SIGN_IN_BUDGET_REQUESTS + 1)]

    def test_a_trusted_proxy_makes_the_forwarded_client_the_key(self, make_deployment):
        deployment = make_deployment(trusted_proxies=TRUSTED_PROXIES)
        first = deployment.client(PROXY_ADDRESS, headers={"x-forwarded-for": "198.51.100.7, 127.0.0.1"})
        second = deployment.client(PROXY_ADDRESS, headers={"x-forwarded-for": "198.51.100.8, 127.0.0.1"})

        statuses = [first.get(AUTH_START_PATH).status_code for _ in range(SIGN_IN_BUDGET_REQUESTS + 1)]

        assert statuses[-1] == HTTPStatus.TOO_MANY_REQUESTS
        assert set(statuses[:-1]) == {HTTPStatus.SEE_OTHER}
        assert second.get(AUTH_START_PATH).status_code == HTTPStatus.SEE_OTHER

    def test_a_forged_leftmost_entry_does_not_choose_the_budget(self, make_deployment):
        deployment = make_deployment(trusted_proxies=TRUSTED_PROXIES)

        statuses = self.exhaust_starts(
            lambda index: deployment.client(
                PROXY_ADDRESS, headers={"x-forwarded-for": f"192.0.2.{index}, 198.51.100.7, 127.0.0.1"}
            )
        )

        assert statuses[-1] == HTTPStatus.TOO_MANY_REQUESTS

    def test_without_trusted_proxies_the_forwarded_header_is_ignored(self, deployment):
        statuses = self.exhaust_starts(
            lambda index: deployment.client(FIRST_ADDRESS, headers={"x-forwarded-for": f"192.0.2.{index}"})
        )

        assert statuses[-1] == HTTPStatus.TOO_MANY_REQUESTS


class TestTrustedProxiesSetting:
    @pytest.mark.parametrize("value", ["not-an-address", "10.0.0.0/33", f"{PROXY_ADDRESS},not-an-address"])
    def test_a_malformed_list_stops_the_start_naming_the_setting_and_never_the_value(self, sign_in_settings, value):
        settings = sign_in_settings(trusted_proxies=value).settings

        with pytest.raises(SignInConfigurationError, match="trusted_proxies") as refusal:
            build_sign_in_configs(settings)

        assert value not in str(refusal.value)
        assert "session_secret_file" not in str(refusal.value)

    def test_without_the_variable_no_proxy_is_trusted_and_the_app_starts(self, sign_in_settings):
        settings = sign_in_settings().settings

        assert settings.trusted_proxies == ""
        assert not build_sign_in_configs(settings).web_auth.trusted_proxies.networks
