import json
import re
import secrets
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
import structlog.testing
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import SecretStr
from webauth.config import WebAuthConfig, install_web_auth_config, web_auth_config
from webauth.dependencies import ConnectionRefusal, admit_session, session_id_from_request
from webauth.ports import AccountEventKind

from hopin.auth.sign_in import (
    LOGIN_FAILED_PAGE,
    LOGOUT_LABEL,
    IssuerTransports,
    build_relying_party,
    build_sign_in_configs,
    install_sign_in,
)
from hopin.auth.stores import (
    InMemoryAccounts,
    InMemorySessions,
    InMemorySignInTransaction,
    LoggingAuditSink,
    ProcessWriteLock,
)
from hopin.config.constants import (
    ADMIN_GROUP,
    AUTH_CALLBACK_PATH,
    AUTH_START_PATH,
    BACKCHANNEL_LOGOUT_PATH,
    HOME_PATH,
    LOGIN_GROUP,
    LOGIN_PATH,
    LOGOUT_PATH,
    NO_ACCESS_PATH,
    RETURN_TO_QUERY_PARAMETER,
    SESSION_LIFETIME_SECONDS,
)
from tests.support.fake_issuer import FakeIssuer
from tests.support.sign_in_settings import TEST_ISSUER, TEST_PUBLIC_ORIGIN, SignInSettings
from tests.support.signed_in import new_id_token

pytestmark = pytest.mark.integration

if TYPE_CHECKING:
    from collections.abc import Callable

    from httpx2 import Response

SESSION_COOKIE = "__Host-session_id"
PENDING_COOKIE_PREFIX = "__Host-oidc_pending_"
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

    def browser(self, *, raise_server_exceptions: bool = True) -> TestClient:
        return TestClient(
            self.app,
            base_url=TEST_PUBLIC_ORIGIN,
            follow_redirects=False,
            raise_server_exceptions=raise_server_exceptions,
        )

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

    def post_logout_token(self, token: str, *, content_type: str = "application/x-www-form-urlencoded") -> Response:
        """What the issuer's back-channel logout sends: a form post with no cookie."""
        fields = {"logout_token": token}
        return self.browser(raise_server_exceptions=False).post(
            BACKCHANNEL_LOGOUT_PATH, content=urlencode(fields), headers={"content-type": content_type}
        )

    def log_out(self, browser: TestClient) -> Response:
        """What the page's log-out button sends: a POST. This harness has no origin check; test_gate proves it."""
        return browser.post(LOGOUT_PATH)

    def who(self, browser: TestClient) -> dict[str, object]:
        return browser.get(WHO_AM_I_PATH).json()


@pytest.fixture
def make_harness(sign_in_settings) -> Callable[..., SignInHarness]:
    def make(
        *,
        key_set_of: FakeIssuer | None = None,
        windows_line_ending: bool = False,
        client_secret: str | None = None,
        audit: LoggingAuditSink | None = None,
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
        sessions = InMemorySessions(accounts, threading.Lock())
        app = FastAPI()
        install_web_auth_config(app, configs.web_auth)
        transaction = InMemorySignInTransaction(
            accounts, sessions, LoggingAuditSink() if audit is None else audit, ProcessWriteLock()
        )
        install_sign_in(app, build_relying_party(configs, transports), transaction)

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
        assert [
            "max-age=0" in attributes for name, attributes in cookies.items() if name.startswith(PENDING_COOKIE_PREFIX)
        ] == [True]
        assert who["user"] == {"id": SUBJECT, "username": USERNAME}
        assert session.expires_at - session.created_at == timedelta(seconds=SESSION_LIFETIME_SECONDS)

    def test_the_id_token_lives_in_the_session_row_and_in_no_cookie(self, harness):
        browser = harness.browser()
        response = harness.sign_in_through(browser)
        issued = json.loads(harness.issuer.exchanges[-1][1].body)["id_token"]
        session = harness.sessions.load(harness.who(browser)["session_id"])
        cookies_set = response.headers.get_list("set-cookie")
        assert session.id_token.get_secret_value() == issued
        assert issued not in "".join(cookies_set) + "".join(browser.cookies.values())
        assert issued not in repr(session)

    @pytest.mark.parametrize(
        ("groups", "admin"),
        [
            ([LOGIN_GROUP], False),
            ([f"{LOGIN_GROUP}/team"], False),
            ([f"{LOGIN_GROUP}/administrators"], False),
            ([ADMIN_GROUP], True),
            ([f"{ADMIN_GROUP}/team"], True),
        ],
    )
    def test_the_role_follows_the_groups_at_each_sign_in(self, harness, groups, admin):
        harness.sign_in_through(harness.browser(), groups=groups)
        expected = harness.config.admin_role if admin else harness.config.user_role
        assert harness.accounts.get(SUBJECT).role == expected

    def test_a_member_who_left_the_admin_group_is_a_user_again_at_the_next_sign_in(self, harness):
        harness.sign_in_through(harness.browser(), groups=[ADMIN_GROUP])
        harness.sign_in_through(harness.browser(), groups=[LOGIN_GROUP])
        assert harness.accounts.get(SUBJECT).role == harness.config.user_role

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


class EndSessionPage(HTMLParser):
    """The form webauth's logout page posts: its action, hidden fields and button text."""

    def __init__(self, markup: str) -> None:
        super().__init__()
        self.action = ""
        self.fields: dict[str, str] = {}
        self.button_label = ""
        self._in_button = False
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self.action = attributes["action"]
        elif tag == "input":
            self.fields[attributes["name"]] = attributes["value"]
        elif tag == "button":
            self._in_button = True

    def handle_endtag(self, tag):
        self._in_button = self._in_button and tag != "button"

    def handle_data(self, data):
        if self._in_button:
            self.button_label += data


class TestLogout:
    def test_a_member_is_signed_out_here_and_gets_the_page_that_posts_their_id_token_to_the_issuer(self, harness):
        browser = harness.browser()
        harness.sign_in_through(browser)
        session_id = harness.who(browser)["session_id"]
        id_token = harness.sessions.load(session_id).id_token.get_secret_value()

        response = harness.log_out(browser)

        page = EndSessionPage(response.text)
        cookies = set_cookies(response)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert page.action == f"{TEST_ISSUER}/protocol/openid-connect/logout"
        assert page.fields == {
            "client_id": harness.sign_in.settings.oidc_client_id,
            "post_logout_redirect_uri": TEST_PUBLIC_ORIGIN + LOGIN_PATH,
            "id_token_hint": id_token,
        }
        assert page.button_label == LOGOUT_LABEL
        assert harness.sessions.load(session_id) is None
        assert harness.who(browser) == {"user": None}
        assert {"max-age=0", "path=/"} <= cookies[SESSION_COOKIE]
        assert set(cookies) == {SESSION_COOKIE}

    def test_a_logout_whose_row_is_gone_sends_no_hint_so_the_issuer_asks_once(self, harness):
        browser = harness.browser()
        harness.sign_in_through(browser)
        harness.sessions.delete(harness.who(browser)["session_id"])

        page = EndSessionPage(harness.log_out(browser).text)

        assert "id_token_hint" not in page.fields

    def test_the_id_token_reaches_neither_a_cookie_nor_a_log_line_on_logout(self, harness):
        browser = harness.browser()
        harness.sign_in_through(browser)
        id_token = harness.sessions.load(harness.who(browser)["session_id"]).id_token.get_secret_value()

        with structlog.testing.capture_logs() as logs:
            response = harness.log_out(browser)

        assert id_token not in "".join(response.headers.get_list("set-cookie")) + json.dumps(logs)


class FailingAuditSink(LoggingAuditSink):
    def account_event(self, event) -> None:
        if event.kind is AccountEventKind.BACKCHANNEL_LOGOUT:
            raise RuntimeError("the audit sink of a test is down")
        super().account_event(event)


class TestBackchannelLogout:
    def sign_in_on_devices(self, harness, subject: str, devices: int = 2) -> list[str]:
        session_ids = []
        for _ in range(devices):
            browser = harness.browser()
            harness.sign_in_through(browser, subject=subject)
            session_ids.append(harness.who(browser)["session_id"])
        return session_ids

    def test_a_valid_token_ends_every_session_and_removes_the_account_of_its_subject_and_no_one_elses(self, harness):
        anna = self.sign_in_on_devices(harness, "anna")
        (other,) = self.sign_in_on_devices(harness, "ben", devices=1)
        ben = harness.accounts.get("ben")

        with structlog.testing.capture_logs() as logs:
            response = harness.post_logout_token(harness.issuer.logout_token("anna"))

        assert response.status_code == 200
        assert [harness.sessions.load(session_id) for session_id in anna] == [None, None]
        assert harness.accounts.get("anna") is None
        assert harness.sessions.load(other) is not None
        assert harness.accounts.get("ben") == ben
        ended = [log for log in logs if log.get("kind") == AccountEventKind.BACKCHANNEL_LOGOUT.value]
        assert [log["subject_id"] for log in ended] == ["anna"]
        assert USERNAME not in json.dumps(logs)

    def test_a_token_for_an_unseen_subject_is_accepted_and_creates_no_account(self, harness):
        response = harness.post_logout_token(harness.issuer.logout_token("stranger"))

        assert response.status_code == 200
        assert harness.accounts.get("stranger") is None

    def test_a_token_for_a_subject_without_a_session_removes_the_account_and_logs_no_logout(self, harness):
        harness.accounts.create_for_subject("anna", USERNAME, role="user")

        with structlog.testing.capture_logs() as logs:
            response = harness.post_logout_token(harness.issuer.logout_token("anna"))

        assert response.status_code == 200
        assert harness.accounts.get("anna") is None
        assert [log for log in logs if log.get("kind") == AccountEventKind.BACKCHANNEL_LOGOUT.value] == []

    def test_the_same_token_sent_again_is_accepted_and_changes_nothing(self, harness):
        self.sign_in_on_devices(harness, "anna", devices=1)
        token = harness.issuer.logout_token("anna")
        harness.post_logout_token(token)

        with structlog.testing.capture_logs() as logs:
            response = harness.post_logout_token(token)

        assert response.status_code == 200
        assert harness.accounts.get("anna") is None
        assert [log for log in logs if log.get("kind") == AccountEventKind.BACKCHANNEL_LOGOUT.value] == []

    def test_a_person_signs_in_again_after_the_erasure_and_gets_a_new_account_and_session(self, harness):
        old_browser = harness.browser()
        harness.sign_in_through(old_browser, subject="anna")
        harness.post_logout_token(harness.issuer.logout_token("anna"))

        with structlog.testing.capture_logs() as logs:
            new_browser = harness.browser()
            harness.sign_in_through(new_browser, subject="anna")

        created = [log for log in logs if log.get("kind") == AccountEventKind.USER_CREATED.value]
        assert [(log["subject_id"], log["role"]) for log in created] == [("anna", "user")]
        assert harness.who(old_browser) == {"user": None}
        assert harness.who(new_browser)["user"] == {"id": "anna", "username": USERNAME}

    @pytest.mark.parametrize(
        "forge",
        [
            lambda harness: FakeIssuer(harness.sign_in).logout_token("anna"),
            lambda harness: harness.issuer.logout_token("anna", audience="another-client"),
            lambda harness: harness.issuer.logout_token("anna", issued_at=int(time.time()) - 300),
        ],
        ids=["foreign-key", "other-audience", "too-old"],
    )
    def test_a_token_the_verifier_refuses_answers_400_and_ends_nothing(self, harness, forge):
        (session_id,) = self.sign_in_on_devices(harness, "anna", devices=1)

        response = harness.post_logout_token(forge(harness))

        assert response.status_code == 400
        assert harness.sessions.load(session_id) is not None
        assert harness.accounts.get("anna") is not None

    def test_a_token_sent_as_json_answers_400_and_ends_nothing(self, harness):
        (session_id,) = self.sign_in_on_devices(harness, "anna", devices=1)

        response = harness.post_logout_token(harness.issuer.logout_token("anna"), content_type="application/json")

        assert response.status_code == 400
        assert harness.sessions.load(session_id) is not None
        assert harness.accounts.get("anna") is not None

    def test_an_unreachable_key_set_answers_400_and_ends_nothing(self, harness):
        account = harness.accounts.create_for_subject("anna", USERNAME, role="user")
        now = datetime.now(UTC)
        session = harness.sessions.create(
            account.id, now=now, expires_at=now + timedelta(hours=1), refresh_token=None, id_token=new_id_token()
        )
        harness.issuer.key_set_is_down = True

        response = harness.post_logout_token(harness.issuer.logout_token("anna"))

        assert response.status_code == 400
        assert harness.sessions.load(session.id) is not None
        assert harness.accounts.get("anna") == account

    def test_an_audit_failure_after_the_deletion_answers_400_and_every_session_stands(self, make_harness):
        harness = make_harness(audit=FailingAuditSink())
        session_ids = self.sign_in_on_devices(harness, "anna", devices=2)

        response = harness.post_logout_token(harness.issuer.logout_token("anna"))

        assert response.status_code == 400
        assert all(harness.sessions.load(session_id) is not None for session_id in session_ids)
        assert harness.accounts.get("anna") is not None


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
