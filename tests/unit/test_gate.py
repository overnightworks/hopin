from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from webauth.config import WebAuthConfig, install_web_auth_config, installed_web_auth_config
from webauth.middleware import install_web_auth_middleware
from webauth.middleware.security_headers import STRICT_TRANSPORT_SECURITY
from webauth.policies import SecurityHeadersPolicy, default_content_security_policy

from hopin.app import create_app
from hopin.auth.gate import session_gate
from hopin.auth.sign_in import build_sign_in_configs
from hopin.auth.stores import SignInStores
from hopin.config.constants import (
    ACCOUNT_PATH,
    AUTH_CALLBACK_PATH,
    AUTH_START_PATH,
    HEALTH_PATH,
    ICE_SERVERS_PATH,
    PERMISSIONS_POLICY,
    WEBSOCKET_PATH,
    SignalType,
)
from tests.sign_in_settings import TEST_ISSUER, TEST_PUBLIC_ORIGIN, SignInSettings
from tests.signed_in import SeededMember, cookie_header, handshake_headers, seed_member

BROWSER_ACCEPT = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
FETCH_ACCEPT = {"accept": "application/json"}
STYLESHEETS = ["/static/css/ui-console.css", "/static/css/hopin.css"]
LOOKALIKE_PATHS = ["/health/", "/HEALTH", f"{TEST_PUBLIC_ORIGIN}//health", "/auth/start/", "/auth/callback/x"]
SESSION_LIFETIME = timedelta(hours=12)


class Application:
    """create_app over stores the test holds, reached through an https client like a browser's."""

    def __init__(self, sign_in: SignInSettings, **options) -> None:
        self.stores = SignInStores.in_memory()
        self.app = create_app(sign_in.settings, stores=self.stores, **options)
        self.config = installed_web_auth_config(self.app)
        self.client = TestClient(self.app, base_url=TEST_PUBLIC_ORIGIN, follow_redirects=False)

    def seed(self) -> SeededMember:
        return seed_member(self.stores, self.config)

    def request(self, method: str, path: str, member: SeededMember | None = None, **headers: str):
        if member is not None:
            headers = {**headers, "cookie": cookie_header(member.set_cookie_headers)}
        return self.client.request(method, path, headers=headers)

    def get(self, path: str, member: SeededMember | None = None, **headers: str):
        return self.request("GET", path, member, **headers)


@pytest.fixture
def application(sign_in_settings: Callable[..., SignInSettings]) -> Application:
    return Application(sign_in_settings())


@contextmanager
def hosted_room(application: Application, member: SeededMember) -> Iterator[str]:
    with application.client.websocket_connect(WEBSOCKET_PATH, headers=handshake_headers(member)) as socket:
        socket.send_json({"type": SignalType.JOIN.value})
        yield str(socket.receive_json()["room_id"])


class TestSignedOut:
    def test_a_navigation_to_the_home_page_is_sent_through_the_sign_in(self, application):
        response = application.get("/", **BROWSER_ACCEPT)
        assert response.status_code == 302
        assert response.headers["location"] == "/auth/start?next=%2F"

    def test_a_navigation_to_a_call_link_is_sent_through_the_sign_in_and_back(self, application):
        response = application.get("/c/abc?x=1", **BROWSER_ACCEPT)
        assert response.status_code == 302
        assert response.headers["location"] == "/auth/start?next=%2Fc%2Fabc%3Fx%3D1"

    @pytest.mark.parametrize(
        ("path", "headers"),
        [
            (ICE_SERVERS_PATH, FETCH_ACCEPT),
            ("/static/js/app.js", FETCH_ACCEPT),
            ("/", FETCH_ACCEPT),
            (ACCOUNT_PATH, FETCH_ACCEPT),
            (ICE_SERVERS_PATH, {"accept": "*/*"}),
        ],
    )
    def test_a_call_that_is_not_a_navigation_gets_401_and_no_location(self, application, path, headers):
        response = application.get(path, **headers)
        assert response.status_code == 401
        assert "location" not in response.headers

    def test_the_health_check_answers_only_its_status(self, application):
        assert application.get(HEALTH_PATH).json() == {"status": "ok"}
        assert application.client.head(HEALTH_PATH).status_code == 200

    def test_the_sign_in_start_is_public(self, application):
        response = application.get(AUTH_START_PATH, **BROWSER_ACCEPT)
        assert response.status_code == 303
        assert response.headers["location"].startswith(TEST_ISSUER)

    @pytest.mark.parametrize("path", STYLESHEETS)
    def test_the_stylesheets_of_the_login_failed_page_are_public(self, application, path):
        assert application.get(path, **BROWSER_ACCEPT).status_code == 200

    @pytest.mark.parametrize("path", LOOKALIKE_PATHS)
    def test_a_path_that_only_looks_like_a_public_path_is_gated(self, application, path):
        response = application.get(path, **BROWSER_ACCEPT)
        assert response.status_code in {302, 401}

    def test_a_foreign_host_header_changes_nothing_about_the_redirect(self, application):
        plain = application.get("/c/abc", **BROWSER_ACCEPT)
        foreign = application.get("/c/abc", **BROWSER_ACCEPT, host="evil.example")
        assert foreign.status_code == 302
        assert foreign.headers["location"] == plain.headers["location"]
        assert all("evil.example" not in value for value in foreign.headers.values())


class TestSignedIn:
    def test_a_member_reaches_every_page_and_the_ice_servers(self, application):
        member = application.seed()
        with hosted_room(application, member) as room_id:
            for path in ["/", f"/c/{room_id}", ICE_SERVERS_PATH, "/static/js/app.js"]:
                assert application.get(path, member, **BROWSER_ACCEPT).status_code == 200, path

    def test_a_session_of_another_application_instance_is_refused(self, sign_in_settings):
        sign_in = sign_in_settings()
        first, second = Application(sign_in), Application(sign_in)
        member = first.seed()
        assert second.get("/", member, **BROWSER_ACCEPT).status_code == 302
        assert second.get(ICE_SERVERS_PATH, member, **FETCH_ACCEPT).status_code == 401


class TestSessionLifetime:
    def test_a_session_ends_after_twelve_hours(self, sign_in_settings):
        sign_in = sign_in_settings()
        stores = SignInStores.in_memory()
        now = [1_800_000_000.0]
        config: WebAuthConfig = replace(build_sign_in_configs(sign_in.settings).web_auth, wall_clock=lambda: now[0])
        member = seed_member(stores, config)
        app = FastAPI()
        install_web_auth_config(app, config)
        install_gate(app, stores)

        @app.get("/")
        def home() -> str:
            return "home"

        client = TestClient(app, base_url=TEST_PUBLIC_ORIGIN, follow_redirects=False)
        headers = {**BROWSER_ACCEPT, "cookie": cookie_header(member.set_cookie_headers)}

        now[0] += SESSION_LIFETIME.total_seconds() - 1
        assert client.get("/", headers=headers).status_code == 200
        now[0] += 2
        assert client.get("/", headers=headers).status_code == 302


def install_gate(app: FastAPI, stores: SignInStores) -> None:
    install_web_auth_middleware(
        app,
        security_headers=SecurityHeadersPolicy(
            content_security_policy=default_content_security_policy(), permissions_policy=PERMISSIONS_POLICY
        ),
        session_gate=session_gate(stores.sessions),
    )


class TestSecurityHeaders:
    @pytest.mark.parametrize(
        ("method", "path", "signed_in", "headers", "status"),
        [
            ("GET", "/", False, BROWSER_ACCEPT, 302),
            ("GET", "/c/abc?x=1", False, BROWSER_ACCEPT, 302),
            ("GET", ICE_SERVERS_PATH, False, FETCH_ACCEPT, 401),
            ("GET", "/static/js/app.js", False, FETCH_ACCEPT, 401),
            ("GET", "/", False, FETCH_ACCEPT, 401),
            ("GET", ACCOUNT_PATH, False, FETCH_ACCEPT, 401),
            ("GET", HEALTH_PATH, False, {}, 200),
            ("HEAD", HEALTH_PATH, False, {}, 200),
            ("GET", AUTH_START_PATH, False, BROWSER_ACCEPT, 303),
            ("GET", AUTH_CALLBACK_PATH, False, {}, 403),
            *[("GET", path, False, BROWSER_ACCEPT, 200) for path in STYLESHEETS],
            *[("GET", path, False, BROWSER_ACCEPT, 302) for path in LOOKALIKE_PATHS],
            ("GET", "/", True, BROWSER_ACCEPT, 200),
            ("GET", ICE_SERVERS_PATH, True, FETCH_ACCEPT, 200),
            ("GET", "/static/js/app.js", True, FETCH_ACCEPT, 200),
            ("GET", "/c/{room}", True, BROWSER_ACCEPT, 200),
            ("GET", "/c/unknown", True, BROWSER_ACCEPT, 404),
        ],
    )
    def test_every_answer_carries_the_headers(self, application, method, path, signed_in, headers, status):
        member = application.seed() if signed_in else None
        with hosted_room(application, member) if member else nullcontext("") as room_id:
            response = application.request(method, path.format(room=room_id), member, **headers)
        assert response.status_code == status
        assert response.headers["content-security-policy"] == default_content_security_policy()
        assert response.headers["permissions-policy"] == PERMISSIONS_POLICY
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert "referrer-policy" in response.headers
        assert response.headers["strict-transport-security"] == STRICT_TRANSPORT_SECURITY
