import secrets

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from webauth.config import WebAuthConfig, installed_web_auth_config
from webauth.dependencies import session_id_from_request

from hopin.app import create_app
from hopin.auth.sign_in import SignInConfigurationError
from hopin.auth.stores import SignInStores
from hopin.config.constants import ACCOUNT_PATH, AUTH_CALLBACK_PATH, AUTH_START_PATH, LOGIN_GROUP
from tests.fake_issuer import FakeIssuer
from tests.sign_in_settings import TEST_PUBLIC_ORIGIN

SESSION_COOKIE = "__Host-session_id"


class TestCreateApp:
    def test_returns_fastapi_application(self, sign_in_settings):
        app = create_app(sign_in_settings().settings)
        assert isinstance(app, FastAPI)

    def test_uses_provided_settings(self, sign_in_settings):
        app = create_app(sign_in_settings(port=9999).settings)
        assert app.state.settings.port == 9999

    def test_installs_the_web_auth_config_built_from_its_settings(self, sign_in_settings):
        sign_in = sign_in_settings()
        config = installed_web_auth_config(create_app(sign_in.settings))
        assert isinstance(config, WebAuthConfig)
        assert config.session_secret.get_secret_value() == sign_in.session_secret

    def test_does_not_start_without_its_sign_in_files(self, sign_in_settings):
        settings = sign_in_settings().settings
        settings.session_secret_file.unlink()
        with pytest.raises(SignInConfigurationError, match="session_secret_file"):
            create_app(settings)

    def test_signs_a_member_in_through_the_issuer_it_is_given(self, sign_in_settings):
        sign_in = sign_in_settings()
        issuer = FakeIssuer(sign_in)
        app = create_app(sign_in.settings, issuer_transports=issuer.transports)
        browser = TestClient(app, base_url=TEST_PUBLIC_ORIGIN, follow_redirects=False)
        start = browser.get(AUTH_START_PATH)
        query = issuer.authorize(start.headers["location"], subject="subject", groups=[LOGIN_GROUP], username="alice")
        callback = browser.get(f"{AUTH_CALLBACK_PATH}?{query}")
        assert start.headers["location"].startswith(sign_in.settings.oidc_issuer)
        assert callback.status_code == 303
        assert any(raw.startswith(f"{SESSION_COOKIE}=") for raw in callback.headers.get_list("set-cookie"))

    def test_account_route_reads_the_sessions_the_sign_in_wrote(self, sign_in_settings):
        sign_in = sign_in_settings()
        issuer = FakeIssuer(sign_in)
        app = create_app(sign_in.settings, issuer_transports=issuer.transports)
        browser = TestClient(app, base_url=TEST_PUBLIC_ORIGIN, follow_redirects=False)
        start = browser.get(AUTH_START_PATH)
        query = issuer.authorize(start.headers["location"], subject="subject", groups=[LOGIN_GROUP], username="alice")
        browser.get(f"{AUTH_CALLBACK_PATH}?{query}")

        answer = browser.get(ACCOUNT_PATH)

        assert (answer.status_code, answer.json()) == (200, {"name": "alice"})

    def test_signs_members_in_to_the_stores_it_is_given(self, sign_in_settings):
        sign_in = sign_in_settings()
        issuer = FakeIssuer(sign_in)
        stores = SignInStores.in_memory()
        app = create_app(sign_in.settings, issuer_transports=issuer.transports, stores=stores)
        browser = TestClient(app, base_url=TEST_PUBLIC_ORIGIN, follow_redirects=False)
        start = browser.get(AUTH_START_PATH)
        query = issuer.authorize(start.headers["location"], subject="subject", groups=[LOGIN_GROUP], username="alice")
        browser.get(f"{AUTH_CALLBACK_PATH}?{query}")
        cookie = f"{SESSION_COOKIE}={browser.cookies[SESSION_COOKIE]}"
        session_id = session_id_from_request(
            Request({"type": "http", "headers": [(b"cookie", cookie.encode())]}), installed_web_auth_config(app)
        )
        assert session_id is not None
        session = stores.sessions.load(session_id)
        assert stores.accounts.get("subject") is not None
        assert session is not None
        assert session.user.id == "subject"

    def test_registers_routes(self, sign_in_settings):
        response = TestClient(create_app(sign_in_settings().settings)).get("/health")
        assert response.status_code == 200

    def test_serves_the_configured_stun_server_as_ice_servers(self, sign_in_settings):
        settings = sign_in_settings(stun_server="stun:stun.example.test:3478").settings
        response = TestClient(create_app(settings)).get("/api/ice-servers")
        assert response.json() == {"iceServers": [{"urls": ["stun:stun.example.test:3478"]}]}

    @pytest.mark.parametrize("path", ["/", "/static/css/ui-console.css", "/static/css/hopin.css", "/static/js/app.js"])
    def test_serves_the_page_and_its_assets(self, sign_in_settings, path):
        assert TestClient(create_app(sign_in_settings().settings)).get(path).status_code == 200

    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    def test_generated_documentation_is_not_served(self, sign_in_settings, path):
        response = TestClient(create_app(sign_in_settings().settings)).get(path)
        assert response.status_code == 404


class TestLoggedExceptions:
    def test_rendered_traceback_leaves_out_the_values_of_local_variables(self, sign_in_settings, capsys):
        create_app(sign_in_settings().settings)
        sentinel = secrets.token_urlsafe(16)

        def fail_holding_the_sentinel():
            held_secret = sentinel
            raise ValueError(len(held_secret))

        try:
            fail_holding_the_sentinel()
        except ValueError:
            structlog.get_logger().exception("failed")

        rendered = capsys.readouterr().out
        assert "ValueError" in rendered
        assert sentinel not in rendered
