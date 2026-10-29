import json
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import pytest
import structlog.testing
from fastapi import FastAPI
from fastapi.testclient import TestClient
from webauth.config import WebAuthConfig, install_web_auth_config, installed_web_auth_config
from webauth.middleware import install_web_auth_middleware
from webauth.middleware.security_headers import STRICT_TRANSPORT_SECURITY
from webauth.policies import CsrfPolicy, SecurityHeadersPolicy, default_content_security_policy
from webauth.ports import AccountEventKind

from hopin.app import create_app
from hopin.auth.gate import session_gate
from hopin.auth.sign_in import LOGIN_FAILED_PAGE, NO_ACCESS_PAGE, SIGNED_OUT_PAGE, build_sign_in_configs
from hopin.auth.stores import SignInStores
from hopin.config.constants import (
    ACCOUNT_PATH,
    AUTH_CALLBACK_PATH,
    AUTH_START_PATH,
    BACKCHANNEL_LOGOUT_PATH,
    HEALTH_PATH,
    ICE_SERVERS_PATH,
    LOGIN_PATH,
    LOGOUT_PATH,
    NO_ACCESS_PATH,
    PERMISSIONS_POLICY,
    WEBSOCKET_PATH,
    SignalType,
)
from hopin.web.kit import KIT_DIR, KIT_PATH
from tests.support.fake_issuer import FakeIssuer
from tests.support.page_build import FIXTURE_SCRIPT_HASH, IMMUTABLE_FILE, VERSION_FILE, fixture_page_build
from tests.support.sign_in_settings import TEST_ISSUER, TEST_PUBLIC_ORIGIN, SignInSettings
from tests.support.signed_in import SeededMember, cookie_header, handshake_headers, seed_member

pytestmark = pytest.mark.integration

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

BROWSER_ACCEPT = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
FETCH_ACCEPT = {"accept": "application/json"}
STYLESHEETS = ["/static/css/ui-console.css", f"{KIT_PATH}/appkit.css", "/static/css/hopin.css"]
KIT_FONTS = [f"{KIT_PATH}/fonts/{font.name}" for font in sorted((KIT_DIR / "fonts").glob("*.woff2"))]
GATED_KIT_PATHS = [
    f"{KIT_PATH}/fonts/oswald-LICENSE",
    f"{KIT_PATH}/fonts/open-sans-LICENSE",
    f"{KIT_PATH}/..%2Fcss/hopin.css",
    f"{KIT_PATH}/appkit.css/",
    f"{KIT_PATH}/appkit.cssx",
    "/static/appkit-0.0.0/appkit.css",
]
FAVICON = "/static/img/hopin.svg"
STATIC_FILES = ["/static/js/app.js", *STYLESHEETS, FAVICON]
LOOKALIKE_PATHS = [
    "/health/",
    "/HEALTH",
    f"{TEST_PUBLIC_ORIGIN}//health",
    "/auth/start/",
    "/auth/callback/x",
    "/login/",
    "/LOGIN",
    "/no-access/x",
    "/static/img/hopin.svg/",
    "/static/img/hopin.svgx",
    "/static/img/other.svg",
    *GATED_KIT_PATHS,
    "/logout/",
    f"{BACKCHANNEL_LOGOUT_PATH}/",
    f"{BACKCHANNEL_LOGOUT_PATH}x",
    BACKCHANNEL_LOGOUT_PATH.upper(),
]
SIGN_IN_PAGES = {LOGIN_PATH: SIGNED_OUT_PAGE, NO_ACCESS_PATH: NO_ACCESS_PAGE}
FORM_POST = {"content-type": "application/x-www-form-urlencoded"}
SAME_ORIGIN_FORM_POST = {**FORM_POST, "origin": TEST_PUBLIC_ORIGIN}
JSON_POST = {"content-type": "application/json"}
CROSS_SITE_FORM_POST = {**FORM_POST, "origin": "https://evil.example"}
END_SESSION_URL = f"{TEST_ISSUER}/protocol/openid-connect/logout"
# The ruled text (Card A, 29.10.2026): kit 3.1.0's announcer style is the one inline style admitted, by its hash.
ANNOUNCER_STYLE_SOURCES = "'unsafe-hashes' 'sha256-S8qMpvofolR8Mpjy4kQvEm7m1q8clzU4dfDH0AmvZjo='"
EXPECTED_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; base-uri 'self'; "
    f"script-src 'self' '{FIXTURE_SCRIPT_HASH}'; "
    f"style-src 'self' {ANNOUNCER_STYLE_SOURCES}; "
    "connect-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self'; "
    "manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'; form-action 'self'"
)
IMMUTABLE_CACHE_CONTROL = "private, max-age=31536000, immutable"
SESSION_LIFETIME = timedelta(hours=12)


class Application:
    """create_app over stores the test holds, reached through an https client like a browser's."""

    def __init__(self, sign_in: SignInSettings, **options) -> None:
        self.stores = SignInStores.in_memory()
        self.issuer = FakeIssuer(sign_in)
        self.app = create_app(
            sign_in.settings,
            page_build=fixture_page_build(),
            stores=self.stores,
            issuer_transports=self.issuer.transports,
            **options,
        )
        self.config = installed_web_auth_config(self.app)
        self.client = TestClient(self.app, base_url=TEST_PUBLIC_ORIGIN, follow_redirects=False)

    def seed(self) -> SeededMember:
        return seed_member(self.stores, self.config)

    def request(self, method: str, path: str, member: SeededMember | None = None, *, body: bytes = b"", **headers: str):
        if member is not None:
            headers = {**headers, "cookie": cookie_header(member.set_cookie_headers)}
        return self.client.request(method, path, headers=headers, content=body)

    def post_logout_token(self, subject: str, **headers: str):
        """What the issuer's back-channel logout sends: a form post with no origin, cookie or fetch metadata."""
        body = urlencode({"logout_token": self.issuer.logout_token(subject)}).encode()
        url = f"http://hopin:8080{BACKCHANNEL_LOGOUT_PATH}"
        return self.request("POST", url, body=body, **{**FORM_POST, **headers})

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

    @pytest.mark.parametrize("path", [*STYLESHEETS, *SIGN_IN_PAGES])
    def test_the_sign_in_pages_and_their_stylesheets_are_public(self, application, path):
        assert application.get(path, **BROWSER_ACCEPT).status_code == 200

    @pytest.mark.parametrize("font", KIT_FONTS)
    def test_each_kit_font_is_public_with_its_content_type(self, application, font):
        response = application.get(font, **BROWSER_ACCEPT)
        assert (response.status_code, response.headers["content-type"]) == (200, "font/woff2")

    def test_the_kit_has_eight_fonts(self):
        assert len(KIT_FONTS) == 8

    def test_the_favicon_loads_signed_out_as_an_svg(self, application):
        response = application.get(FAVICON, **BROWSER_ACCEPT)
        assert (response.status_code, response.headers["content-type"]) == (200, "image/svg+xml")

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


class TestSignInPages:
    @pytest.mark.parametrize("signed_in", [False, True])
    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    @pytest.mark.parametrize("path", SIGN_IN_PAGES)
    def test_the_page_is_the_same_static_page_for_everyone_and_never_redirects(
        self, application, path, method, signed_in
    ):
        member = application.seed() if signed_in else None

        response = application.request(method, path, member, **BROWSER_ACCEPT)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "location" not in response.headers
        assert response.content == (SIGN_IN_PAGES[path].read_bytes() if method == "GET" else b"")
        assert member is None or application.stores.sessions.load(member.session.id) is not None


class TestLogout:
    def cleared_cookie_paths(self, response):
        cleared = {}
        for raw in response.headers.get_list("set-cookie"):
            _, *attributes = raw.split("; ")
            if "max-age=0" in (attribute.lower() for attribute in attributes):
                path = next(a for a in attributes if a.lower().startswith("path="))
                cleared[raw.split("=", 1)[0]] = f"Path={path.split('=', 1)[1]}"
        return cleared

    def expected_cleared_paths(self, application):
        return {application.config.session_cookie_name: "Path=/"}

    def test_a_member_gets_the_end_session_page_and_loses_the_session_and_its_cookie(self, application):
        member = application.seed()

        response = application.request("POST", LOGOUT_PATH, member, **SAME_ORIGIN_FORM_POST)

        assert response.status_code == 200
        assert f'<form method="post" action="{END_SESSION_URL}">' in response.text
        assert response.headers["cache-control"] == "no-store"
        assert "form-action https://issuer.test.invalid" in response.headers["content-security-policy"]
        assert application.stores.sessions.load(member.session.id) is None
        assert self.cleared_cookie_paths(response) == self.expected_cleared_paths(application)

    @pytest.mark.parametrize("session", ["none", "deleted", "forged"])
    def test_a_person_without_a_usable_session_still_gets_the_end_session_page_and_cleared_cookies(
        self, application, session
    ):
        member = application.seed() if session == "deleted" else None
        if member is not None:
            application.stores.sessions.delete(member.session.id)
        headers = dict(SAME_ORIGIN_FORM_POST)
        if session == "forged":
            headers["cookie"] = f"{application.config.session_cookie_name}=forged"

        response = application.request("POST", LOGOUT_PATH, member, **headers)

        assert response.status_code == 200
        assert f'action="{END_SESSION_URL}"' in response.text
        assert "id_token_hint" not in response.text
        assert self.cleared_cookie_paths(response) == self.expected_cleared_paths(application)

    @pytest.mark.parametrize(
        "headers",
        [
            CROSS_SITE_FORM_POST,
            {**FORM_POST, "sec-fetch-site": "cross-site"},
            FORM_POST,
            JSON_POST,
            {**FORM_POST, "sec-fetch-site": "same-site", "origin": "https://evil.test.invalid"},
            {**FORM_POST, "referer": "https://evil.test.invalid/x"},
        ],
        ids=[
            "foreign-origin",
            "cross-site",
            "no-origin-evidence",
            "json-without-evidence",
            "sibling-subdomain",
            "referer-only",
        ],
    )
    def test_a_logout_another_site_triggers_is_refused_and_keeps_the_session(self, application, headers):
        member = application.seed()

        response = application.request("POST", LOGOUT_PATH, member, **headers)

        assert response.status_code == 403
        assert "location" not in response.headers
        assert self.cleared_cookie_paths(response) == {}
        assert application.stores.sessions.load(member.session.id) is not None

    def test_a_logout_the_browser_marks_same_origin_is_accepted_without_an_origin_header(self, application):
        member = application.seed()

        response = application.request("POST", LOGOUT_PATH, member, **FORM_POST, **{"sec-fetch-site": "same-origin"})

        assert response.status_code == 200
        assert application.stores.sessions.load(member.session.id) is None

    def test_a_get_is_not_a_logout(self, application):
        member = application.seed()

        response = application.get(LOGOUT_PATH, member, **BROWSER_ACCEPT)

        assert response.status_code == 405
        assert application.stores.sessions.load(member.session.id) is not None

    def test_a_get_signed_out_is_sent_through_the_sign_in(self, application):
        response = application.get(LOGOUT_PATH, **BROWSER_ACCEPT)

        assert response.status_code == 302
        assert response.headers["location"] == "/auth/start?next=%2Flogout"


class TestBackchannelLogout:
    def test_the_issuer_as_it_posts_ends_the_sessions_without_origin_cookie_or_fetch_metadata(self, application):
        member = application.seed()
        other_device = seed_member(application.stores, application.config, account=member.account)
        other_member = application.seed()

        with structlog.testing.capture_logs() as logs:
            response = application.post_logout_token(member.account.id, host="hopin:8080")

        assert response.status_code == 200
        assert application.stores.sessions.load(member.session.id) is None
        assert application.stores.sessions.load(other_device.session.id) is None
        assert application.stores.sessions.load(other_member.session.id) is not None
        ended = [log for log in logs if log.get("kind") == AccountEventKind.BACKCHANNEL_LOGOUT.value]
        assert [log["subject_id"] for log in ended] == [member.account.id]
        assert member.account.username not in json.dumps(logs)

    @pytest.mark.parametrize(
        "path", [f"{BACKCHANNEL_LOGOUT_PATH}/", f"{BACKCHANNEL_LOGOUT_PATH}x", BACKCHANNEL_LOGOUT_PATH.upper()]
    )
    def test_a_lookalike_path_is_not_exempt_from_the_origin_check(self, application, path):
        assert application.request("POST", path, **FORM_POST).status_code == 403

    def test_the_exemption_leaves_a_cross_site_logout_refused(self, application):
        assert application.request("POST", LOGOUT_PATH, **CROSS_SITE_FORM_POST).status_code == 403

    @pytest.mark.parametrize(
        "path", [f"{BACKCHANNEL_LOGOUT_PATH}x", f"{BACKCHANNEL_LOGOUT_PATH}/", BACKCHANNEL_LOGOUT_PATH.upper()]
    )
    def test_a_lookalike_path_is_not_public_for_a_post(self, application, path):
        response = application.request("POST", path, **{"sec-fetch-site": "same-origin"})
        assert response.status_code == 401
        assert "location" not in response.headers

    @pytest.mark.parametrize(
        ("headers", "status"), [(BROWSER_ACCEPT, 302), (FETCH_ACCEPT, 401)], ids=["navigation", "fetch"]
    )
    def test_a_get_is_gated_and_never_reaches_the_route(self, application, headers, status):
        response = application.get(BACKCHANNEL_LOGOUT_PATH, **headers)
        assert response.status_code == status
        if status == 401:
            assert "location" not in response.headers
        else:
            assert response.headers["location"].startswith(AUTH_START_PATH)

    def test_a_form_of_32_kib_is_accepted_and_one_byte_over_is_refused(self, application):
        padded = b"logout_token=" + b"a" * (32 * 1024 - len(b"logout_token="))
        accepted = application.request("POST", BACKCHANNEL_LOGOUT_PATH, body=padded, **FORM_POST)
        refused = application.request("POST", BACKCHANNEL_LOGOUT_PATH, body=padded + b"a", **FORM_POST)
        assert accepted.status_code == 400
        assert refused.status_code == 413

    def test_a_form_of_33_kib_to_the_path_is_refused(self, application):
        body = b"logout_token=" + b"a" * (33 * 1024)
        assert application.request("POST", BACKCHANNEL_LOGOUT_PATH, body=body, **FORM_POST).status_code == 413

    def test_every_other_path_takes_only_4_kib(self, application):
        over = b"a" * (5 * 1024)
        assert application.request("POST", LOGOUT_PATH, body=over, **SAME_ORIGIN_FORM_POST).status_code == 413

    def test_every_answer_of_the_path_is_not_cached(self, application):
        oversized = b"a" * (33 * 1024)
        answers = {
            200: application.post_logout_token("nobody"),
            400: application.request("POST", BACKCHANNEL_LOGOUT_PATH, **FORM_POST),
            413: application.request("POST", BACKCHANNEL_LOGOUT_PATH, body=oversized, **FORM_POST),
        }
        for status, response in answers.items():
            assert response.status_code == status
            assert response.headers["cache-control"] == "no-store"

    def test_other_paths_keep_their_cache_headers(self, application):
        assert application.get(HEALTH_PATH).headers.get("cache-control") != "no-store"


class TestRevalidation:
    @pytest.mark.parametrize("path", ["/", "/c/abc", "/login", "/no-access", VERSION_FILE, *STATIC_FILES])
    def test_every_page_and_static_file_answers_no_cache_with_its_etag(self, application, path):
        member = application.seed()
        with hosted_room(application, member) as room_id:
            response = application.get(path.replace("abc", room_id), member, **BROWSER_ACCEPT)
        assert (response.headers["cache-control"], "etag" in response.headers) == ("no-cache", True)

    @pytest.mark.parametrize("path", STATIC_FILES)
    def test_a_static_file_still_answers_304_to_its_etag(self, application, path):
        member = application.seed()
        etag = application.get(path, member).headers["etag"]
        assert application.get(path, member, **{"if-none-match": etag}).status_code == 304

    def test_the_admitted_callback_that_sets_the_session_cookie_is_never_stored(self, application):
        start = application.get(AUTH_START_PATH, **BROWSER_ACCEPT)
        query = application.issuer.authorize(
            start.headers["location"], subject="anna", groups=["/apps/hopin"], username="anna"
        )

        callback = application.get(f"{AUTH_CALLBACK_PATH}?{query}", **BROWSER_ACCEPT)

        assert (callback.status_code, callback.headers["location"]) == (303, "/")
        assert callback.headers["cache-control"] == "no-store"
        assert start.headers["cache-control"] == "no-store"

    def test_a_refused_callback_answer_is_not_stored(self, application):
        response = application.get(AUTH_CALLBACK_PATH, **BROWSER_ACCEPT)
        assert (response.status_code, response.content) == (403, LOGIN_FAILED_PAGE.read_bytes())
        assert response.headers["cache-control"] == "no-store"


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


class TestPageBuildAssets:
    def test_a_signed_out_fetch_of_an_immutable_file_is_refused_and_stored_for_no_long_time(self, application):
        response = application.get(IMMUTABLE_FILE, **FETCH_ACCEPT)

        assert response.status_code == 401
        assert "max-age" not in response.headers.get("cache-control", "")

    def test_a_signed_in_fetch_of_an_immutable_file_is_kept_a_year_by_the_browser_alone(self, application):
        response = application.get(IMMUTABLE_FILE, application.seed(), **FETCH_ACCEPT)

        assert (response.status_code, response.headers["cache-control"]) == (200, IMMUTABLE_CACHE_CONTROL)

    def test_the_not_modified_answer_for_an_immutable_file_carries_the_same_cache_control(self, application):
        member = application.seed()
        etag = application.get(IMMUTABLE_FILE, member).headers["etag"]

        response = application.get(IMMUTABLE_FILE, member, **{"if-none-match": etag})

        assert (response.status_code, response.headers["cache-control"]) == (304, IMMUTABLE_CACHE_CONTROL)

    def test_a_signed_in_miss_below_the_immutable_directory_is_stored_for_no_long_time(self, application):
        response = application.get("/_app/immutable/absent.js", application.seed(), **FETCH_ACCEPT)

        assert response.status_code == 404
        assert "max-age" not in response.headers.get("cache-control", "")

    def test_the_version_file_is_revalidated_on_every_use(self, application):
        response = application.get(VERSION_FILE, application.seed(), **FETCH_ACCEPT)

        assert (response.status_code, response.headers["cache-control"]) == (200, "no-cache")


def install_gate(app: FastAPI, stores: SignInStores) -> None:
    install_web_auth_middleware(
        app,
        security_headers=SecurityHeadersPolicy(
            content_security_policy=default_content_security_policy(), permissions_policy=PERMISSIONS_POLICY
        ),
        csrf_origin=CsrfPolicy(),
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
            ("GET", IMMUTABLE_FILE, False, FETCH_ACCEPT, 401),
            ("GET", "/", False, FETCH_ACCEPT, 401),
            ("GET", ACCOUNT_PATH, False, FETCH_ACCEPT, 401),
            ("GET", HEALTH_PATH, False, {}, 200),
            ("HEAD", HEALTH_PATH, False, {}, 200),
            ("GET", AUTH_START_PATH, False, BROWSER_ACCEPT, 303),
            ("GET", AUTH_CALLBACK_PATH, False, {}, 403),
            *[("GET", path, False, BROWSER_ACCEPT, 200) for path in [*STYLESHEETS, *SIGN_IN_PAGES]],
            ("POST", LOGOUT_PATH, False, CROSS_SITE_FORM_POST, 403),
            *[("GET", path, False, BROWSER_ACCEPT, 302) for path in LOOKALIKE_PATHS],
            ("GET", "/", True, BROWSER_ACCEPT, 200),
            ("GET", ICE_SERVERS_PATH, True, FETCH_ACCEPT, 200),
            ("GET", "/static/js/app.js", True, FETCH_ACCEPT, 200),
            ("GET", IMMUTABLE_FILE, True, FETCH_ACCEPT, 200),
            ("GET", "/c/{room}", True, BROWSER_ACCEPT, 200),
            ("GET", "/c/unknown", True, BROWSER_ACCEPT, 404),
        ],
    )
    def test_every_answer_carries_the_headers(self, application, method, path, signed_in, headers, status):
        member = application.seed() if signed_in else None
        with hosted_room(application, member) if member else nullcontext("") as room_id:
            response = application.request(method, path.format(room=room_id), member, **headers)
        assert response.status_code == status
        assert response.headers["content-security-policy"] == EXPECTED_CONTENT_SECURITY_POLICY
        assert response.headers["permissions-policy"] == PERMISSIONS_POLICY
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert "referrer-policy" in response.headers
        assert response.headers["strict-transport-security"] == STRICT_TRANSPORT_SECURITY
