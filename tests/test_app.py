import asyncio
import secrets
import threading
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
import structlog
import structlog.testing
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from webauth.config import WebAuthConfig, installed_web_auth_config
from webauth.dependencies import session_id_from_request

from hopin.app import create_app
from hopin.auth.stores import SignInStores
from hopin.config.constants import (
    ACCOUNT_PATH,
    AUTH_CALLBACK_PATH,
    AUTH_START_PATH,
    LOGIN_GROUP,
    SESSION_SWEEP_SECONDS,
    WEBSOCKET_PATH,
    SignalType,
)
from hopin.config.secret_files import StartRefusedError
from hopin.web.kit import KIT_PATH
from tests.support.fake_issuer import FakeIssuer
from tests.support.page_build import FIXTURE_SCRIPT_HASH, fixture_page_build
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN
from tests.support.signed_in import cookie_header, new_id_token, seed_member

pytestmark = pytest.mark.integration

if TYPE_CHECKING:
    from hopin.config.settings import ServerSettings

SESSION_COOKIE = "__Host-session_id"


def signed_in_client(settings: ServerSettings) -> TestClient:
    stores = SignInStores.in_memory()
    app = create_app(settings, page_build=fixture_page_build(), stores=stores)
    member = seed_member(stores, installed_web_auth_config(app))
    headers = {"cookie": cookie_header(member.set_cookie_headers)}
    return TestClient(app, base_url=TEST_PUBLIC_ORIGIN, headers=headers, follow_redirects=False)


class TestCreateApp:
    def test_returns_fastapi_application(self, sign_in_settings):
        app = create_app(sign_in_settings().settings, page_build=fixture_page_build())
        assert isinstance(app, FastAPI)

    def test_uses_provided_settings(self, sign_in_settings):
        app = create_app(sign_in_settings(port=9999).settings, page_build=fixture_page_build())
        assert app.state.settings.port == 9999

    def test_installs_the_web_auth_config_built_from_its_settings(self, sign_in_settings):
        sign_in = sign_in_settings()
        config = installed_web_auth_config(create_app(sign_in.settings, page_build=fixture_page_build()))
        assert isinstance(config, WebAuthConfig)
        assert config.session_secret.get_secret_value() == sign_in.session_secret

    def test_does_not_start_without_its_sign_in_files(self, sign_in_settings):
        settings = sign_in_settings().settings
        settings.session_secret_file.unlink()
        page_build = fixture_page_build()
        with pytest.raises(StartRefusedError, match="session_secret_file"):
            create_app(settings, page_build=page_build)

    def test_signs_a_member_in_through_the_issuer_it_is_given(self, sign_in_settings):
        sign_in = sign_in_settings()
        issuer = FakeIssuer(sign_in)
        app = create_app(sign_in.settings, page_build=fixture_page_build(), issuer_transports=issuer.transports)
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
        app = create_app(sign_in.settings, page_build=fixture_page_build(), issuer_transports=issuer.transports)
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
        app = create_app(
            sign_in.settings, page_build=fixture_page_build(), issuer_transports=issuer.transports, stores=stores
        )
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

    def test_admits_the_member_the_sign_in_router_signed_in_to_its_pages(self, sign_in_settings):
        sign_in = sign_in_settings()
        issuer = FakeIssuer(sign_in)
        app = create_app(sign_in.settings, page_build=fixture_page_build(), issuer_transports=issuer.transports)
        browser = TestClient(app, base_url=TEST_PUBLIC_ORIGIN, follow_redirects=False)
        start = browser.get(AUTH_START_PATH)
        query = issuer.authorize(start.headers["location"], subject="subject", groups=[LOGIN_GROUP], username="alice")
        callback = browser.get(f"{AUTH_CALLBACK_PATH}?{query}")
        handshake = {"origin": TEST_PUBLIC_ORIGIN, "cookie": cookie_header(callback.headers.get_list("set-cookie"))}
        with browser.websocket_connect(WEBSOCKET_PATH, headers=handshake) as socket:
            socket.send_json({"type": SignalType.JOIN.value})
            room_id = socket.receive_json()["room_id"]
            for path in ["/", f"/c/{room_id}"]:
                response = browser.get(path, headers={"accept": "text/html"})
                assert (response.status_code, "location" in response.headers) == (200, False)

    def test_registers_routes(self, sign_in_settings):
        response = TestClient(create_app(sign_in_settings().settings, page_build=fixture_page_build())).get("/health")
        assert response.status_code == 200

    def test_serves_the_configured_stun_server_as_ice_servers(self, sign_in_settings):
        settings = sign_in_settings(stun_server="stun:stun.example.test:3478").settings
        response = signed_in_client(settings).get("/api/ice-servers")
        assert response.json() == {"iceServers": [{"urls": ["stun:stun.example.test:3478"]}]}

    def test_with_turn_off_it_serves_stun_only_and_reads_no_turn_file(self, sign_in_settings, tmp_path):
        settings = sign_in_settings(
            turn_key_id_file=tmp_path / "missing-key-id", turn_api_token_file=tmp_path / "missing-api-token"
        ).settings
        response = signed_in_client(settings).get("/api/ice-servers")
        assert response.json() == {"iceServers": [{"urls": [settings.stun_server]}]}

    @pytest.mark.parametrize(
        "path",
        ["/", "/static/css/ui-console.css", f"{KIT_PATH}/appkit.css", "/static/css/hopin.css", "/static/js/app.js"],
    )
    def test_serves_the_page_and_its_assets(self, sign_in_settings, path):
        assert signed_in_client(sign_in_settings().settings).get(path).status_code == 200

    def test_the_policy_admits_the_scripts_of_the_shell_by_their_hash(self, sign_in_settings):
        response = signed_in_client(sign_in_settings().settings).get("/health")

        assert f"script-src 'self' '{FIXTURE_SCRIPT_HASH}';" in response.headers["content-security-policy"]

    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    def test_generated_documentation_is_not_served(self, sign_in_settings, path):
        assert signed_in_client(sign_in_settings().settings).get(path).status_code == 404


WAIT_CAP_SECONDS = 5


class QueuingTimer:
    def __init__(self, callback):
        self.callback = callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class QueuingScheduler:
    """Holds timers until the test queues the callback as a task, whether or not the timer was cancelled."""

    def __init__(self):
        self.timer = None

    def call_later(self, _delay_seconds, callback):
        self.timer = QueuingTimer(callback)
        return self.timer

    def queue_due_callback(self):
        return asyncio.get_running_loop().create_task(self.timer.callback())


class TestExpiredSessionSweep:
    @pytest.fixture
    def swept(self, sign_in_settings, scheduler):
        stores = SignInStores.in_memory()
        app = create_app(
            sign_in_settings().settings, page_build=fixture_page_build(), stores=stores, scheduler=scheduler
        )
        account = stores.accounts.create_for_subject("subject", "name", "user")
        now = datetime.now(UTC)
        created_at = now - timedelta(hours=2)
        expired = stores.sessions.create(
            account.id, now=created_at, expires_at=now - timedelta(hours=1), refresh_token=None, id_token=new_id_token()
        )
        live = stores.sessions.create(
            account.id, now=created_at, expires_at=now + timedelta(hours=1), refresh_token=None, id_token=new_id_token()
        )
        return app, stores.sessions, expired, live

    def test_the_lifespan_removes_expired_sessions_every_interval_and_keeps_live_ones(self, swept, scheduler):
        app, sessions, expired, live = swept
        with TestClient(app):
            asyncio.run(scheduler.advance(SESSION_SWEEP_SECONDS))
            assert [sessions.load(expired.id), sessions.load(live.id) is not None] == [None, True]

    def test_the_lifespan_sweeps_again_every_following_interval(self, swept, scheduler):
        app, sessions, _, _ = swept
        with TestClient(app):
            asyncio.run(scheduler.advance(SESSION_SWEEP_SECONDS))
            now = datetime.now(UTC)
            later = sessions.create(
                "subject",
                now=now - timedelta(hours=2),
                expires_at=now - timedelta(hours=1),
                refresh_token=None,
                id_token=new_id_token(),
            )
            asyncio.run(scheduler.advance(SESSION_SWEEP_SECONDS))
            assert sessions.load(later.id) is None

    def test_the_lifespan_stops_a_sweep_that_is_still_running_at_shutdown(self, swept, scheduler, monkeypatch):
        app, sessions, _, _ = swept
        scan_started, scan_released = threading.Event(), threading.Event()
        scans = []
        delete_expired = sessions.delete_expired

        def held_scan(now):
            scans.append(now)
            scan_started.set()
            scan_released.wait()
            return delete_expired(now)

        monkeypatch.setattr(sessions, "delete_expired", held_scan)

        async def shut_down_during_the_scan():
            try:
                async with app.router.lifespan_context(app):
                    running = asyncio.create_task(scheduler.advance(SESSION_SWEEP_SECONDS))
                    await asyncio.to_thread(scan_started.wait, WAIT_CAP_SECONDS)
                    assert scan_started.is_set()
            finally:
                scan_released.set()
            await running
            timer_pending_after_the_scan = scheduler.has_pending_timer()
            await scheduler.advance(SESSION_SWEEP_SECONDS * 2)
            return timer_pending_after_the_scan

        timer_pending_after_the_scan = asyncio.run(shut_down_during_the_scan())
        assert (len(scans), timer_pending_after_the_scan, scheduler.has_pending_timer()) == (1, False, False)

    def test_a_sweep_queued_before_shutdown_removes_nothing_once_the_lifespan_has_stopped(self, sign_in_settings):
        stores = SignInStores.in_memory()
        scheduler = QueuingScheduler()
        app = create_app(
            sign_in_settings().settings, page_build=fixture_page_build(), stores=stores, scheduler=scheduler
        )
        now = datetime.now(UTC)
        account = stores.accounts.create_for_subject("subject", "name", "user")
        expired = stores.sessions.create(
            account.id,
            now=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
            refresh_token=None,
            id_token=new_id_token(),
        )

        async def shut_down_with_the_sweep_queued():
            async with app.router.lifespan_context(app):
                queued = scheduler.queue_due_callback()
            await queued

        asyncio.run(shut_down_with_the_sweep_queued())
        assert (stores.sessions.load(expired.id) is not None, scheduler.timer.cancelled) == (True, True)

    def test_the_lifespan_stops_the_sweep_at_shutdown(self, swept, scheduler):
        app, sessions, expired, _ = swept
        with TestClient(app):
            pass
        asyncio.run(scheduler.advance(SESSION_SWEEP_SECONDS))
        assert sessions.load(expired.id) is not None

    def test_a_sweep_that_removed_nothing_logs_nothing(self, swept, scheduler):
        app, sessions, expired, _ = swept
        sessions.delete(expired.id)
        with structlog.testing.capture_logs() as logs, TestClient(app):
            asyncio.run(scheduler.advance(SESSION_SWEEP_SECONDS))
        assert [entry["event"] for entry in logs if entry["event"] == "expired_sessions_swept"] == []

    def test_a_sweep_that_removed_sessions_logs_only_the_count(self, swept, scheduler):
        app, *_ = swept
        with structlog.testing.capture_logs() as logs, TestClient(app):
            asyncio.run(scheduler.advance(SESSION_SWEEP_SECONDS))
        assert [entry for entry in logs if entry["event"] == "expired_sessions_swept"] == [
            {"event": "expired_sessions_swept", "log_level": "info", "removed": 1}
        ]


class TestLoggedExceptions:
    def test_rendered_traceback_leaves_out_the_values_of_local_variables(self, sign_in_settings, capsys):
        create_app(sign_in_settings().settings, page_build=fixture_page_build())
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
