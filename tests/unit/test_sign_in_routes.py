import json
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
import structlog.testing
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import SecretStr
from webauth.config import WebAuthConfig, install_web_auth_config, web_auth_config
from webauth.dependencies import ConnectionRefusal, admit_session, session_id_from_request

from hopin.auth.sign_in import LOGIN_FAILED_PAGE, IssuerTransports, build_sign_in_configs, create_sign_in_router
from hopin.auth.stores import InMemoryAccounts, InMemorySessions, LoggingAuditSink, ProcessWriteLock
from hopin.config.constants import (
    AUTH_CALLBACK_PATH,
    AUTH_START_PATH,
    HOME_PATH,
    LOGIN_GROUP,
    LOGIN_PATH,
    LOGOUT_PATH,
    NO_ACCESS_PATH,
    RETURN_TO_QUERY_PARAMETER,
    SESSION_LIFETIME_SECONDS,
)
from tests.fake_issuer import FakeIssuer
from tests.sign_in_settings import TEST_ISSUER, TEST_PUBLIC_ORIGIN, SignInSettings

SESSION_COOKIE = "__Host-session_id"
PENDING_COOKIE_PREFIX = "__Host-oidc_pending_"
HINT_COOKIE = "__Secure-oidc_id_token_hint"
WHO_AM_I_PATH = "/who-am-i"
SUBJECT = "subject-1"
USERNAME = "alice"


@dataclass
class SignInHarness:
    sign_in: SignInSettings
    issuer: FakeIssuer
    accounts: InMemoryAccounts
    sessions: InMemorySessions
    config: WebAuthConfig
    app: FastAPI
    started: list[Response] = field(default_factory=list)

    def browser(self) -> TestClient:
        return TestClient(self.app, base_url=TEST_PUBLIC_ORIGIN, follow_redirects=False)

    def start(self, browser: TestClient, next_path: str | None = None) -> Response:
        params = {} if next_path is None else {RETURN_TO_QUERY_PARAMETER: next_path}
        response = browser.get(AUTH_START_PATH, params=params)
        self.started.append(response)
        return response

    def sign_in_through(
        self,
        browser: TestClient,
        *,
        subject: str = SUBJECT,
        groups: list[str] | str | None = (LOGIN_GROUP,),
        username: str = USERNAME,
        next_path: str | None = None,
    ) -> Response:
        start = self.start(browser, next_path)
        query = self.issuer.authorize(start.headers["location"], subject=subject, groups=groups, username=username)
        return browser.get(f"{AUTH_CALLBACK_PATH}?{query}")

    def who(self, browser: TestClient) -> dict[str, object]:
        return browser.get(WHO_AM_I_PATH).json()


@pytest.fixture
def make_harness(sign_in_settings) -> Callable[..., SignInHarness]:
    def make(
        *, key_set_of: FakeIssuer | None = None, windows_line_ending: bool = False, client_secret: str | None = None
    ) -> SignInHarness:
        sign_in = sign_in_settings()
        if client_secret is not None:
            sign_in = replace(sign_in, client_secret=client_secret)
        sign_in.settings.oidc_client_secret_file.write_text(
            sign_in.client_secret + ("\r\n" if windows_line_ending else "\n")
        )
        configs = build_sign_in_configs(sign_in.settings)
        issuer = FakeIssuer(sign_in)
        transports = issuer.transports
        if key_set_of is not None:
            transports = IssuerTransports(transports.post_token_request, key_set_of.fetch_key_set)
        accounts = InMemoryAccounts()
        sessions = InMemorySessions(accounts)
        app = FastAPI()
        install_web_auth_config(app, configs.web_auth)
        app.include_router(
            create_sign_in_router(
                configs.oidc_client,
                configs.web_auth,
                transports,
                accounts=accounts,
                sessions=sessions,
                lock=ProcessWriteLock(),
                audit=LoggingAuditSink(),
            )
        )

        @app.get(WHO_AM_I_PATH)
        def who_am_i(request: Request) -> dict[str, object]:
            config = web_auth_config(request)
            session_id = session_id_from_request(request, config)
            if session_id is None:
                return {"user": None}
            admitted = admit_session(session_id, sessions=sessions, config=config)
            if isinstance(admitted, ConnectionRefusal):
                return {"user": None}
            return {"user": {"id": admitted.id, "username": admitted.username}, "session_id": session_id}

        return SignInHarness(sign_in, issuer, accounts, sessions, configs.web_auth, app)

    return make


@pytest.fixture
def harness(make_harness) -> SignInHarness:
    return make_harness()


def set_cookies(response: Response) -> dict[str, set[str]]:
    """Each Set-Cookie of the response as its name and its lowercased attributes."""
    cookies = {}
    for raw in response.headers.get_list("set-cookie"):
        name_and_value, *attributes = raw.split("; ")
        cookies[name_and_value.split("=", 1)[0]] = {attribute.lower() for attribute in attributes}
    return cookies


def pending_cookie_values(response: Response) -> dict[str, str]:
    raw_cookies = (raw.split("; ")[0].split("=", 1) for raw in response.headers.get_list("set-cookie"))
    return {name: value for name, value in raw_cookies if name.startswith(PENDING_COOKIE_PREFIX)}


def with_query_value(url: str, name: str, value: str) -> str:
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    query[name] = [value]
    return parts._replace(query=urlencode(query, doseq=True)).geturl()


def cookie_scope(raw_cookie: str) -> tuple[str, frozenset[str]]:
    """A Set-Cookie as its name and the attributes that say where and how the browser keeps it."""
    name_and_value, *attributes = raw_cookie.split("; ")
    kept = {a.lower() for a in attributes if not a.lower().startswith(("max-age", "expires"))}
    return name_and_value.split("=", 1)[0], frozenset(kept)


def assert_refused(harness: SignInHarness, response: Response, *, cleared_under_start_name: bool = True) -> None:
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")
    assert response.content == LOGIN_FAILED_PAGE.read_bytes()
    assert_pending_login_cleared(harness, response, cleared_under_start_name=cleared_under_start_name)


def assert_pending_login_cleared(
    harness: SignInHarness, response: Response, *, cleared_under_start_name: bool = True
) -> None:
    cookies = set_cookies(response)
    assert SESSION_COOKIE not in cookies
    assert HINT_COOKIE not in cookies
    set_by_start = {
        cookie_scope(raw)
        for started in harness.started
        for raw in started.headers.get_list("set-cookie")
        if raw.startswith(PENDING_COOKIE_PREFIX)
    }
    cleared = [raw for raw in response.headers.get_list("set-cookie") if raw.startswith(PENDING_COOKIE_PREFIX)]
    assert len(cleared) == 1
    assert "max-age=0" in cleared[0].lower()
    cleared_name, cleared_attributes = cookie_scope(cleared[0])
    if cleared_under_start_name:
        assert (cleared_name, cleared_attributes) in set_by_start
    else:
        assert cleared_attributes in {attributes for _, attributes in set_by_start}


class TestStart:
    def test_sends_the_browser_to_the_authorization_endpoint_with_a_pending_login(self, harness):
        response = harness.start(harness.browser(), "/c/abc")
        location = urlsplit(response.headers["location"])
        query = parse_qs(location.query)
        assert response.status_code == 303
        assert f"{location.scheme}://{location.netloc}{location.path}" == (
            f"{harness.sign_in.settings.oidc_issuer}/protocol/openid-connect/auth"
        )
        assert {name: query[name] for name in ("client_id", "redirect_uri", "response_type", "scope")} == {
            "client_id": [harness.sign_in.settings.oidc_client_id],
            "redirect_uri": [TEST_PUBLIC_ORIGIN + AUTH_CALLBACK_PATH],
            "response_type": ["code"],
            "scope": ["openid profile groups"],
        }
        assert query["code_challenge_method"] == ["S256"]
        assert {"state", "nonce", "code_challenge"} <= query.keys()
        pending = [
            attributes for name, attributes in set_cookies(response).items() if name.startswith(PENDING_COOKIE_PREFIX)
        ]
        assert len(pending) == 1
        assert {"httponly", "secure"} <= pending[0]


class TestAdmittedLogin:
    @pytest.mark.parametrize("group", [LOGIN_GROUP, f"{LOGIN_GROUP}/team"])
    def test_a_member_gets_a_session_and_lands_on_the_requested_page(self, harness, group):
        browser = harness.browser()
        response = harness.sign_in_through(browser, groups=[group], next_path="/c/abc")
        cookies = set_cookies(response)
        who = harness.who(browser)
        session = harness.sessions.load(who["session_id"])
        assert response.status_code == 303
        assert response.headers["location"] == "/c/abc"
        assert {"httponly", "secure", "samesite=lax", "path=/", f"max-age={SESSION_LIFETIME_SECONDS}"} <= cookies[
            SESSION_COOKIE
        ]
        assert "path=/logout" in cookies[HINT_COOKIE]
        assert [
            "max-age=0" in attributes for name, attributes in cookies.items() if name.startswith(PENDING_COOKIE_PREFIX)
        ] == [True]
        assert who["user"] == {"id": SUBJECT, "username": USERNAME}
        assert session.expires_at - session.created_at == timedelta(seconds=SESSION_LIFETIME_SECONDS)

    def test_the_issued_refresh_token_is_held_in_the_session_row_as_a_secret(self, harness):
        browser = harness.browser()
        harness.sign_in_through(browser)
        session = harness.sessions.load(harness.who(browser)["session_id"])
        assert isinstance(session.refresh_token, SecretStr)
        assert session.refresh_token.get_secret_value() == harness.issuer.refresh_token

    @pytest.mark.parametrize(
        ("requested", "landing"),
        [
            (None, HOME_PATH),
            ("/c/abc?x=1", "/c/abc?x=1"),
            ("//evil.example/x", HOME_PATH),
            ("/\\evil.example/x", HOME_PATH),
            ("https://evil.example/", HOME_PATH),
            ("evil.example", HOME_PATH),
        ],
    )
    def test_the_login_lands_only_on_a_page_of_this_app(self, harness, requested, landing):
        browser = harness.browser()
        response = harness.sign_in_through(browser, next_path=requested)
        assert (response.status_code, response.headers["location"]) == (303, landing)
        assert harness.who(browser)["user"] == {"id": SUBJECT, "username": USERNAME}

    def test_two_subjects_with_one_username_both_get_a_session(self, harness):
        first, second = harness.browser(), harness.browser()
        harness.sign_in_through(first, subject="first")
        harness.sign_in_through(second, subject="second")
        assert [harness.who(browser)["user"] for browser in (first, second)] == [
            {"id": "first", "username": USERNAME},
            {"id": "second", "username": USERNAME},
        ]


class TestLogout:
    def test_a_member_is_signed_out_here_and_sent_to_the_issuers_end_session_with_their_hint(self, harness):
        browser = harness.browser()
        callback = harness.sign_in_through(browser)
        hint = browser.cookies.get(HINT_COOKIE, path=LOGOUT_PATH)
        session_id = harness.who(browser)["session_id"]

        response = browser.post(LOGOUT_PATH)

        location = urlsplit(response.headers["location"])
        query = parse_qs(location.query)
        cookies = set_cookies(response)
        assert callback.status_code == 303
        assert response.status_code == 303
        assert (
            f"{location.scheme}://{location.netloc}{location.path}" == f"{TEST_ISSUER}/protocol/openid-connect/logout"
        )
        assert query == {
            "client_id": [harness.sign_in.settings.oidc_client_id],
            "post_logout_redirect_uri": [TEST_PUBLIC_ORIGIN + LOGIN_PATH],
            "id_token_hint": [hint],
        }
        assert harness.sessions.load(session_id) is None
        assert harness.who(browser) == {"user": None}
        assert {"max-age=0", "path=/"} <= cookies[SESSION_COOKIE]
        assert {"max-age=0", "path=/logout"} <= cookies[HINT_COOKIE]


class TestRefusedLogin:
    @pytest.mark.parametrize(
        "groups", [[], None, [f"{LOGIN_GROUP}2"], ["/apps"], ["/apps/songmaker"]], ids=lambda groups: str(groups)
    )
    def test_a_non_member_is_sent_to_no_access_and_gets_no_account(self, harness, groups):
        browser = harness.browser()
        response = harness.sign_in_through(browser, groups=groups)
        assert response.status_code == 303
        assert response.headers["location"] == NO_ACCESS_PATH
        assert_pending_login_cleared(harness, response)
        assert harness.who(browser) == {"user": None}
        assert harness.accounts.get(SUBJECT) is None

    def test_an_altered_state_is_refused_before_any_token_request(self, harness):
        browser = harness.browser()
        start = harness.start(browser)
        query = harness.issuer.authorize(
            start.headers["location"], subject=SUBJECT, groups=[LOGIN_GROUP], username=USERNAME
        )
        response = browser.get(f"{AUTH_CALLBACK_PATH}?{query.replace('state=', 'state=x')}")
        # webauth clears the cookie named for the state it was handed, which is not the one the start set
        assert_refused(harness, response, cleared_under_start_name=False)
        assert harness.issuer.exchanges == []

    def test_another_browsers_callback_is_refused_and_spends_nothing_of_the_first(self, harness):
        owner, intruder = harness.browser(), harness.browser()
        start = harness.start(owner)
        query = harness.issuer.authorize(
            start.headers["location"], subject=SUBJECT, groups=[LOGIN_GROUP], username=USERNAME
        )
        refused = intruder.get(f"{AUTH_CALLBACK_PATH}?{query}")
        assert harness.issuer.exchanges == []
        admitted = owner.get(f"{AUTH_CALLBACK_PATH}?{query}")
        assert_refused(harness, refused, cleared_under_start_name=False)
        assert harness.who(intruder) == {"user": None}
        assert admitted.status_code == 303
        assert harness.who(owner)["user"] == {"id": SUBJECT, "username": USERNAME}

    @pytest.mark.parametrize("claim", ["nonce", "code_challenge"])
    def test_a_swapped_nonce_or_challenge_is_refused(self, harness, claim):
        browser = harness.browser()
        start = harness.start(browser)
        swapped = with_query_value(start.headers["location"], claim, "swapped")
        query = harness.issuer.authorize(swapped, subject=SUBJECT, groups=[LOGIN_GROUP], username=USERNAME)
        response = browser.get(f"{AUTH_CALLBACK_PATH}?{query}")
        assert_refused(harness, response)
        assert harness.who(browser) == {"user": None}

    def test_try_again_on_the_login_failed_page_signs_in_to_the_home_not_the_first_requested_page(self, harness):
        browser = harness.browser()
        harness.start(browser, "/c/first-requested")
        refused = browser.get(f"{AUTH_CALLBACK_PATH}?state=forged&code=x")
        assert_refused(harness, refused, cleared_under_start_name=False)
        again = re.search(r'href="([^"]+)">Try again</a>', refused.text)
        assert again is not None

        start = browser.get(again.group(1))
        query = harness.issuer.authorize(
            start.headers["location"], subject=SUBJECT, groups=[LOGIN_GROUP], username=USERNAME
        )
        landing = browser.get(f"{AUTH_CALLBACK_PATH}?{query}")

        assert (landing.status_code, landing.headers["location"]) == (303, HOME_PATH)

    def test_a_replayed_callback_is_refused_and_leaves_the_first_session(self, harness):
        browser = harness.browser()
        start = harness.start(browser)
        query = harness.issuer.authorize(
            start.headers["location"], subject=SUBJECT, groups=[LOGIN_GROUP], username=USERNAME
        )
        ((pending_name, pending_value),) = pending_cookie_values(start).items()
        assert browser.get(f"{AUTH_CALLBACK_PATH}?{query}").status_code == 303
        with_copy = browser.get(f"{AUTH_CALLBACK_PATH}?{query}", headers={"cookie": f"{pending_name}={pending_value}"})
        requests_after_copy = len(harness.issuer.exchanges)
        without_copy = browser.get(f"{AUTH_CALLBACK_PATH}?{query}")
        assert_refused(harness, with_copy)
        assert_refused(harness, without_copy)
        assert len(harness.issuer.exchanges) == requests_after_copy == 2
        assert harness.who(browser)["user"] == {"id": SUBJECT, "username": USERNAME}
        assert harness.sessions.delete_for_user(SUBJECT) == 1

    @pytest.mark.parametrize(
        "breakage",
        [
            "token endpoint down",
            "key set down",
            "token signed by another issuer",
            "access denied",
            "groups claim is a string",
        ],
    )
    def test_a_broken_sign_in_gets_login_failed_and_no_session(self, make_harness, sign_in_settings, breakage):
        other_issuer = FakeIssuer(sign_in_settings())
        harness = make_harness(key_set_of=other_issuer if breakage == "token signed by another issuer" else None)
        harness.issuer.token_endpoint_is_down = breakage == "token endpoint down"
        harness.issuer.key_set_is_down = breakage == "key set down"
        browser = harness.browser()
        start = harness.start(browser)
        query = harness.issuer.authorize(
            start.headers["location"],
            subject=SUBJECT,
            groups=LOGIN_GROUP if breakage == "groups claim is a string" else [LOGIN_GROUP],
            username=USERNAME,
        )
        if breakage == "access denied":
            query = f"error=access_denied&state={parse_qs(urlsplit(start.headers['location']).query)['state'][0]}"
        response = browser.get(f"{AUTH_CALLBACK_PATH}?{query}")
        assert_refused(harness, response)
        assert harness.who(browser) == {"user": None}


class TestIssuerRecord:
    def test_a_token_request_to_an_endpoint_that_is_down_is_still_recorded(self, harness):
        harness.issuer.token_endpoint_is_down = True
        harness.sign_in_through(harness.browser())
        assert len(harness.issuer.exchanges) == 1


class TestSessionLifecycle:
    def test_a_second_sign_in_in_one_browser_ends_the_earlier_session(self, harness):
        browser = harness.browser()
        harness.sign_in_through(browser)
        earlier = harness.who(browser)["session_id"]
        harness.sign_in_through(browser)
        assert (
            admit_session(earlier, sessions=harness.sessions, config=harness.config)
            is ConnectionRefusal.SESSION_ENDED_OR_EXPIRED
        )
        assert harness.who(browser)["session_id"] != earlier
        assert harness.sessions.delete_for_user(SUBJECT) == 1

    def test_one_subject_signed_in_on_three_clients_keeps_all_three_sessions(self, harness):
        browsers = [harness.browser() for _ in range(3)]
        for browser in browsers:
            harness.sign_in_through(browser)
        assert [harness.who(browser)["user"]["id"] for browser in browsers] == [SUBJECT] * 3
        assert harness.sessions.delete_for_user(SUBJECT) == 3


class TestLogs:
    def test_the_first_sign_in_logs_the_subject_but_never_the_username_and_a_second_logs_no_creation(self, harness):
        with structlog.testing.capture_logs() as logs:
            harness.sign_in_through(harness.browser())
            harness.sign_in_through(harness.browser())
        created = [log for log in logs if log.get("kind") == "user_created"]
        assert [(log["subject_id"], log["role"]) for log in created] == [(SUBJECT, "user")]
        assert USERNAME not in json.dumps(logs)

    @pytest.mark.parametrize(
        ("groups", "endpoint_is_down", "reason"),
        [([], False, "not-a-member"), ([LOGIN_GROUP], True, "token-endpoint-failed")],
    )
    def test_a_refusal_logs_one_line_with_its_reason_and_no_identity(self, harness, groups, endpoint_is_down, reason):
        harness.issuer.token_endpoint_is_down = endpoint_is_down
        with structlog.testing.capture_logs() as logs:
            harness.sign_in_through(harness.browser(), groups=groups)
        refusals = [log for log in logs if log["event"] == "sign_in_refused"]
        assert [log["reason"] for log in refusals] == [reason]
        assert SUBJECT not in json.dumps(refusals)
        assert USERNAME not in json.dumps(refusals)

    def test_a_client_secret_with_a_carriage_return_shows_as_a_refused_token_request(self, make_harness):
        harness = make_harness(windows_line_ending=True)
        with structlog.testing.capture_logs() as logs:
            response = harness.sign_in_through(harness.browser())
        assert_refused(harness, response)
        assert [(log["reason"], log["idp_error"]) for log in logs if log["event"] == "sign_in_refused"] == [
            ("token-request-refused", "invalid_client")
        ]

    def test_no_secret_reaches_a_log_or_a_response_body(self, harness):
        browser, refused_browser = harness.browser(), harness.browser()
        with structlog.testing.capture_logs() as logs:
            start = harness.start(browser)
            query = harness.issuer.authorize(
                start.headers["location"], subject=SUBJECT, groups=[LOGIN_GROUP], username=USERNAME
            )
            admitted = browser.get(f"{AUTH_CALLBACK_PATH}?{query}")
            refused_start = harness.start(refused_browser)
            refused_query = harness.issuer.authorize(
                refused_start.headers["location"], subject="other", groups=[], username=USERNAME
            )
            refused = refused_browser.get(f"{AUTH_CALLBACK_PATH}?{refused_query}")
        assert len(harness.issuer.exchanges) == 2
        secrets_of_the_run = [
            harness.sign_in.client_secret,
            harness.sign_in.session_secret,
            harness.issuer.refresh_token,
            harness.who(browser)["session_id"],
        ]
        for started, callback_query, (request, answer) in zip(
            (start, refused_start), (query, refused_query), harness.issuer.exchanges, strict=True
        ):
            callback = parse_qs(callback_query)
            secrets_of_the_run += [
                callback["code"][0],
                callback["state"][0],
                parse_qs(urlsplit(started.headers["location"]).query)["nonce"][0],
                request.form["code_verifier"],
                json.loads(answer.body)["id_token"],
            ]
        everything_visible = json.dumps(logs) + admitted.text + refused.text + start.text + refused_start.text
        assert [secret for secret in secrets_of_the_run if secret in everything_visible] == []

    def test_a_client_secret_with_plus_slash_and_percent_is_accepted_as_an_issuer_decodes_it(self, make_harness):
        harness = make_harness(client_secret="a+b/c%2Fd%e" + secrets.token_urlsafe(8))
        browser = harness.browser()
        response = harness.sign_in_through(browser)
        assert response.status_code == 303
        assert harness.who(browser)["user"] == {"id": SUBJECT, "username": USERNAME}
