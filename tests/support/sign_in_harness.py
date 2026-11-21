from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi.testclient import TestClient

from hopin.config.constants import (
    AUTH_CALLBACK_PATH,
    AUTH_START_PATH,
    BACKCHANNEL_LOGOUT_PATH,
    LOGIN_GROUP,
    LOGOUT_PATH,
)
from hopin.config.web_policy import RETURN_TO_QUERY_PARAMETER
from tests.support.page_build import SOURCE_LANGUAGE_DIRECTORY
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN, SignInSettings

if TYPE_CHECKING:
    from fastapi import FastAPI
    from httpx2 import Response
    from webauth.config import WebAuthConfig
    from webauth.single_process_stores import SingleProcessAccountStore, SingleProcessSessionStore

    from tests.support.fake_issuer import FakeIssuer

SESSION_COOKIE = "__Host-session_id"
PENDING_COOKIE_PREFIX = "__Host-oidc_pending_"
WHO_AM_I_PATH = "/who-am-i"
SUBJECT = "subject-1"
USERNAME = "alice"


@dataclass
class SignInHarness:
    sign_in: SignInSettings
    issuer: FakeIssuer
    accounts: SingleProcessAccountStore
    sessions: SingleProcessSessionStore
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

    def start(self, browser: TestClient, next_path: str | None = None, **headers: str) -> Response:
        params = {} if next_path is None else {RETURN_TO_QUERY_PARAMETER: next_path}
        response = browser.get(AUTH_START_PATH, params=params, headers=headers)
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
        """What the page's log-out button sends: a POST from the app's own origin."""
        return browser.post(LOGOUT_PATH, headers={"origin": TEST_PUBLIC_ORIGIN})

    def who(self, browser: TestClient) -> dict[str, object]:
        return browser.get(WHO_AM_I_PATH).json()


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
    assert response.content == (SOURCE_LANGUAGE_DIRECTORY / "login-failed.html").read_bytes()
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
