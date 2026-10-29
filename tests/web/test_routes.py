import hashlib
import re
import tomllib
from functools import partial
from pathlib import Path
from xml.etree import ElementTree

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hopin.auth.sign_in import LOGIN_FAILED_PAGE, NO_ACCESS_PAGE, SIGNED_OUT_PAGE
from hopin.config.constants import (
    APP_ASSET_PATH,
    AUTH_START_PATH,
    CALL_PATH,
    HEALTH_PATH,
    HOME_PATH,
    LOGOUT_PATH,
    REPLAY_LOG_LIMIT,
    RETURN_TO_QUERY_PARAMETER,
    SESSION_ENDED_CLOSE_CODE,
    STATIC_PATH,
    WEBSOCKET_PATH,
    ErrorReason,
    RoomClosedReason,
    SignalType,
)
from hopin.rooms.models import Account
from hopin.signaling.features import FeatureClashError
from hopin.signaling.handler import SignalingHandler
from hopin.signaling.scheduler import AsyncioScheduler
from hopin.web.kit import KIT_PATH
from hopin.web.routes import STATIC_DIR, create_router
from hopin.web.wire import WIRE_MODULE_PATH
from tests.support.page_build import fixture_page_build
from tests.support.page_inventory import (
    CSS_COMMENT,
    LEGAL_URLS,
    NEW_TAB_LINK_ATTRIBUTES,
    UI_CONSOLE_CSS,
    defined_class_names,
    inventory_of,
)
from tests.support.signaling_fakes import RecordingFeature, SocketAccountResolver
from tests.support.wire_module import served_exports

pytestmark = pytest.mark.integration


@pytest.fixture
def signaling_handler(room_manager):
    return SignalingHandler(room_manager, SocketAccountResolver(), AsyncioScheduler())


@pytest.fixture
def app(signaling_handler, room_manager):
    application = FastAPI()
    application.include_router(create_router(signaling_handler, room_manager, fixture_page_build()))
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


class TestCreateRoutes:
    def test_returns_five_routes_and_the_kit_static_and_app_mounts(self, signaling_handler, room_manager):
        router = create_router(signaling_handler, room_manager, fixture_page_build())
        assert len(router.routes) == 8

    def test_static_dir_points_to_web_static(self):
        assert STATIC_DIR.name == "static"
        assert STATIC_DIR.parent.name == "web"


class TestWireModule:
    def test_serves_the_python_vocabulary_as_a_javascript_module(self, client):
        response = client.get(WIRE_MODULE_PATH)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/javascript")
        assert served_exports(response.text) == {
            "SIGNAL_TYPE": {member.name: member.value for member in SignalType},
            "ERROR_REASON": {member.name: member.value for member in ErrorReason},
            "ROOM_CLOSED_REASON": {member.name: member.value for member in RoomClosedReason},
            "SESSION_ENDED_CLOSE_CODE": SESSION_ENDED_CLOSE_CODE,
            "WEBSOCKET_PATH": WEBSOCKET_PATH,
            "AUTH_START_PATH": AUTH_START_PATH,
            "RETURN_TO_QUERY_PARAMETER": RETURN_TO_QUERY_PARAMETER,
            "REPLAY_LOG_LIMIT": REPLAY_LOG_LIMIT,
            "DESCRIPTION_TYPES": ["answer", "offer"],
            "SUPERSEDED_BY_A_DESCRIPTION": ["answer", "ice_candidate", "offer"],
            "FEATURE_MODULES": [],
        }

    def test_a_feature_export_named_like_a_core_export_is_refused_at_start(self, room_manager):
        clashing = partial(RecordingFeature, wire_exports={"ERROR_REASON": {"SAMPLE_REASON": "sample_reason"}})
        handler = SignalingHandler(room_manager, SocketAccountResolver(), AsyncioScheduler(), clashing)

        page_build = fixture_page_build()

        with pytest.raises(FeatureClashError, match="ERROR_REASON"):
            create_router(handler, room_manager, page_build)


class TestHealthEndpoint:
    def test_returns_ok_status(self, client):
        response = client.get(HEALTH_PATH)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestHomePage:
    def test_returns_html(self, client):
        response = client.get(HOME_PATH)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_answers_the_page_builds_shell_with_its_etag(self, client):
        page_build = fixture_page_build()

        response = client.get(HOME_PATH)

        assert (response.content, response.headers["etag"]) == (page_build.shell, page_build.shell_etag)


class TestCallPage:
    def test_returns_html_for_a_known_room(self, client, room_manager):
        room = room_manager.create_room("h1", Account(id="h1", name="h1"))
        response = client.get(f"{CALL_PATH}/{room.room_id}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_answers_the_same_shell_for_a_known_room(self, client, room_manager):
        room = room_manager.create_room("h1", Account(id="h1", name="h1"))

        response = client.get(f"{CALL_PATH}/{room.room_id}")

        assert response.content == fixture_page_build().shell

    def test_returns_404_for_an_unknown_room(self, client):
        response = client.get(f"{CALL_PATH}/nonexistent")
        assert response.status_code == 404

    @pytest.mark.parametrize("path", ["/call", "/join/x"])
    def test_the_retired_addresses_answer_404(self, client, path):
        assert client.get(path).status_code == 404


class TestStaticAssets:
    @pytest.mark.parametrize(
        ("asset", "content_type"),
        [
            ("css/ui-console.css", "text/css"),
            ("css/hopin.css", "text/css"),
        ],
    )
    def test_serves_asset_with_its_content_type(self, client, asset, content_type):
        response = client.get(f"{STATIC_PATH}/{asset}")
        assert response.status_code == 200
        assert content_type in response.headers["content-type"]

    @pytest.mark.parametrize(
        ("asset", "content_type"),
        [("appkit.css", "text/css"), ("fonts/oswald-latin-700-normal.woff2", "font/woff2")],
    )
    def test_serves_the_kit_under_its_versioned_path(self, client, asset, content_type):
        response = client.get(f"{KIT_PATH}/{asset}")
        assert response.status_code == 200
        assert content_type in response.headers["content-type"]

    def test_does_not_serve_the_page_from_the_static_mount(self, client):
        assert client.get(f"{STATIC_PATH}/index.html").status_code == 404

    def test_does_not_serve_the_page_code_the_bundle_owns(self, client):
        assert client.get(f"{STATIC_PATH}/js/app.js").status_code == 404


class TestAppAssets:
    @pytest.mark.parametrize(("asset", "content_type"), [("immutable/x.js", "javascript"), ("version.json", "json")])
    def test_serves_the_build_files_under_the_app_path(self, client, asset, content_type):
        response = client.get(f"{APP_ASSET_PATH}/{asset}")
        assert response.status_code == 200
        assert content_type in response.headers["content-type"]


class TestHeadRequests:
    @pytest.mark.parametrize("path", [HOME_PATH, HEALTH_PATH, f"{STATIC_PATH}/css/hopin.css"])
    def test_head_answers_ok(self, client, path):
        assert client.head(path).status_code == 200


SAME_ORIGIN_ABSOLUTE_PATH = re.compile(r"^/[^/]")
UI_CONSOLE_SHA256 = "7033d912b69862165210c34512164aeff0422a53d566c5492ddad0bed8b97d39"
STYLESHEETS_DIR = STATIC_DIR / "assets" / "css"
# HopIn's own stylesheets: the page's and each one a feature's browser module loads.
OWN_STYLESHEETS = sorted(set((STATIC_DIR / "assets").rglob("*.css")) - {UI_CONSOLE_CSS})
LOCK_FILE = Path(__file__).parents[2] / "uv.lock"
CSS_MOTION_DECLARATION = re.compile(r"(?:transition|animation)(?:-duration)?\s*:([^;}]*)")
CSS_MOTION_TOKEN_REFERENCE = re.compile(r"var\(--motion-[\w-]+\)")
CSS_LITERAL_DURATION = re.compile(r"(?<![\w.-])\d*\.?\d+m?s\b")
CSS_VARIABLE_REFERENCE = re.compile(r"var\(")
CSS_ROOT_MOTION_TOKEN = re.compile(r"(--motion-[\w-]+)\s*:")
CSS_ZEROED_MOTION_TOKEN = re.compile(r"(--motion-[\w-]+)\s*:\s*0s\b")
CSS_REDUCED_MOTION_BLOCK = re.compile(r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?\})\s*\}", re.DOTALL)
CSS_ROOT_BLOCK = re.compile(r":root\s*\{([^{}]*)\}")
CSS_CUSTOM_PROPERTY_DEFINITION = re.compile(r"(--[\w-]+)\s*:")
CSS_CUSTOM_PROPERTY_READ = re.compile(r"var\((--[\w-]+)")
CSS_COLOR_SCHEME_DECLARATION = re.compile(r"(?<![\w-])color-scheme\s*:")
UI_CONSOLE_TOKEN_STILL_READ = "--sans"
FAVICON_FILE = STATIC_DIR / "assets" / "img" / "hopin.svg"
FAVICON_URL = f"{STATIC_PATH}/img/hopin.svg"


def untokened_motion_durations(stylesheet: str) -> list[str]:
    declarations = CSS_MOTION_DECLARATION.findall(CSS_COMMENT.sub("", stylesheet))
    untokened = [CSS_MOTION_TOKEN_REFERENCE.sub("", declaration) for declaration in declarations]
    return [
        duration
        for declaration in untokened
        for duration in CSS_LITERAL_DURATION.findall(declaration) + CSS_VARIABLE_REFERENCE.findall(declaration)
    ]


def motion_tokens_left_running_under_reduced_motion(stylesheet: str) -> set[str]:
    stylesheet = CSS_COMMENT.sub("", stylesheet)
    reduced_block = "".join(CSS_REDUCED_MOTION_BLOCK.findall(stylesheet))
    defined = {name for block in CSS_ROOT_BLOCK.findall(stylesheet) for name in CSS_ROOT_MOTION_TOKEN.findall(block)}
    return defined - set(CSS_ZEROED_MOTION_TOKEN.findall(reduced_block))


@pytest.fixture(params=sorted(STATIC_DIR.glob("*.html")), ids=lambda page: page.name)
def every_page_inventory(request):
    return inventory_of(request.param.read_text())


class TestPageIsCspReady:
    def test_has_no_inline_script_style_or_handler(self, every_page_inventory):
        assert every_page_inventory.inline_scripts == 0
        assert every_page_inventory.style_elements == 0
        assert every_page_inventory.forbidden_attributes == []

    def test_fetches_only_same_origin_absolute_paths(self, every_page_inventory):
        assert every_page_inventory.fetched_urls
        assert all(SAME_ORIGIN_ABSOLUTE_PATH.match(url) for url in every_page_inventory.fetched_urls)


class TestLoginFailedPage:
    @pytest.fixture
    def markup(self):
        return LOGIN_FAILED_PAGE.read_text()

    @pytest.fixture
    def inventory(self, markup):
        return inventory_of(markup)

    def test_links_to_a_new_sign_in_in_the_same_tab_and_to_both_legal_pages_in_a_new_tab(self, inventory):
        again, *legal = inventory.links
        assert again["href"] == AUTH_START_PATH
        assert "target" not in again
        assert [link["href"] for link in legal] == list(LEGAL_URLS)
        assert all(link[name] == value for link in legal for name, value in NEW_TAB_LINK_ATTRIBUTES.items())

    def test_runs_no_script_and_never_redirects_by_itself(self, markup):
        assert "<script" not in markup.lower()
        assert "http-equiv" not in markup


class TestSignedOutPage:
    @pytest.fixture
    def markup(self):
        return SIGNED_OUT_PAGE.read_text()

    def test_links_to_a_new_sign_in_in_the_same_tab_and_to_both_legal_pages_in_a_new_tab(self, markup):
        again, *legal = inventory_of(markup).links
        assert again["href"] == AUTH_START_PATH
        assert "target" not in again
        assert [link["href"] for link in legal] == list(LEGAL_URLS)
        assert all(link[name] == value for link in legal for name, value in NEW_TAB_LINK_ATTRIBUTES.items())

    def test_runs_no_script_and_never_redirects_by_itself(self, markup):
        assert "<script" not in markup.lower()
        assert "http-equiv" not in markup


class TestNoAccessPage:
    @pytest.fixture
    def markup(self):
        return NO_ACCESS_PAGE.read_text()

    def test_offers_one_log_out_form_to_the_logout_route_and_no_other_way_out(self, markup):
        inventory = inventory_of(markup)
        assert [(form["method"], form["action"]) for form in inventory.forms] == [("post", LOGOUT_PATH)]
        assert inventory.submit_buttons == 1
        assert [link["href"] for link in inventory.links] == list(LEGAL_URLS)
        assert all(link[name] == value for link in inventory.links for name, value in NEW_TAB_LINK_ATTRIBUTES.items())
        assert "Back" not in markup

    def test_runs_no_script_and_never_redirects_by_itself(self, markup):
        assert "<script" not in markup.lower()
        assert "http-equiv" not in markup


class TestPageShell:
    def test_every_page_loads_ui_console_then_the_kit_then_hopin(self, every_page_inventory):
        assert every_page_inventory.stylesheets == [
            f"{STATIC_PATH}/css/ui-console.css",
            f"{KIT_PATH}/appkit.css",
            f"{STATIC_PATH}/css/hopin.css",
        ]

    def test_the_kit_path_carries_the_version_the_lock_records(self):
        packages = tomllib.loads(LOCK_FILE.read_text())["package"]
        locked = next(package["version"] for package in packages if package["name"] == "overnightworks-appkit")
        assert KIT_PATH.endswith(f"/appkit-{locked}")

    def test_every_page_links_the_brand_mark_as_its_one_svg_icon(self, every_page_inventory):
        assert every_page_inventory.icons == [{"rel": "icon", "type": "image/svg+xml", "href": FAVICON_URL}]

    def test_the_icon_is_an_svg_document_in_utf8_with_the_brand_mark_viewbox(self):
        # The file is the repository's own, never untrusted input.
        root = ElementTree.fromstring(FAVICON_FILE.read_bytes().decode("utf-8"))  # noqa: S314 - parses the repository own favicon and no untrusted input
        assert (root.tag, root.get("viewBox")) == ("{http://www.w3.org/2000/svg}svg", "0 0 24 24")

    def test_vendored_ui_console_is_byte_identical_to_its_release(self):
        assert hashlib.sha256(UI_CONSOLE_CSS.read_bytes()).hexdigest() == UI_CONSOLE_SHA256

    @pytest.mark.parametrize("stylesheet_path", OWN_STYLESHEETS, ids=lambda path: path.name)
    def test_reads_no_ui_console_token_that_the_kit_does_not_also_define(self, client, stylesheet_path):
        stylesheet = CSS_COMMENT.sub("", stylesheet_path.read_text())
        ui_console_tokens = set(CSS_CUSTOM_PROPERTY_DEFINITION.findall(CSS_COMMENT.sub("", UI_CONSOLE_CSS.read_text())))
        kit_tokens = set(
            CSS_CUSTOM_PROPERTY_DEFINITION.findall(CSS_COMMENT.sub("", client.get(f"{KIT_PATH}/appkit.css").text))
        )
        own_tokens = set(CSS_CUSTOM_PROPERTY_DEFINITION.findall(stylesheet))
        read_tokens = set(CSS_CUSTOM_PROPERTY_READ.findall(stylesheet))
        assert ui_console_tokens
        assert (read_tokens & ui_console_tokens) - own_tokens - kit_tokens - {UI_CONSOLE_TOKEN_STILL_READ} == set()

    def test_defines_no_token_and_no_color_scheme_the_kit_owns(self, client):
        stylesheet = CSS_COMMENT.sub("", (STYLESHEETS_DIR / "hopin.css").read_text())
        kit = CSS_COMMENT.sub("", client.get(f"{KIT_PATH}/appkit.css").text)
        kit_tokens = set(CSS_CUSTOM_PROPERTY_DEFINITION.findall(kit))
        assert kit_tokens
        assert set(CSS_CUSTOM_PROPERTY_DEFINITION.findall(stylesheet)) & kit_tokens == set()
        assert CSS_COLOR_SCHEME_DECLARATION.search(stylesheet) is None

    def test_defines_no_class_name_that_ui_console_defines(self):
        vendored = defined_class_names(UI_CONSOLE_CSS.read_text())
        own_stylesheet = {name for path in OWN_STYLESHEETS for name in defined_class_names(path.read_text())}
        assert vendored
        assert vendored & own_stylesheet == set()

    @pytest.mark.parametrize("stylesheet_path", OWN_STYLESHEETS, ids=lambda path: path.name)
    def test_gives_every_transition_and_animation_its_duration_from_a_motion_token(self, stylesheet_path):
        stylesheet = stylesheet_path.read_text()
        assert CSS_MOTION_DECLARATION.search(stylesheet)
        assert untokened_motion_durations(stylesheet) == []

    @pytest.mark.parametrize(
        "motion_declaration",
        [
            ".a { transition: opacity 0.2s; }",
            ".a { transition: left var(--motion-snap), top .28s ease; }",
            ".a { animation: spin 1.5s infinite; }",
            ".a { transition-duration: 150ms; }",
            ".a { animation-duration: 2s; }",
            ".a { transition: opacity var(--fade); }",
        ],
    )
    def test_finds_a_duration_that_is_no_motion_token_in_a_motion_declaration(self, motion_declaration):
        assert untokened_motion_durations(motion_declaration)

    def test_accepts_a_motion_declaration_that_reads_only_motion_tokens(self):
        motion_declaration = ".a { transition: left var(--motion-snap) ease, top var(--motion-snap); }"
        assert untokened_motion_durations(motion_declaration) == []

    def test_zeroes_every_motion_token_when_reduced_motion_is_asked_for(self):
        stylesheet = (STYLESHEETS_DIR / "hopin.css").read_text()
        assert CSS_ROOT_BLOCK.search(stylesheet)
        assert motion_tokens_left_running_under_reduced_motion(stylesheet) == set()

    def test_finds_a_motion_token_that_reduced_motion_leaves_unzeroed(self):
        stylesheet = (
            ":root { --motion-snap: .28s; --motion-ring: 1.8s; }"
            "@media (prefers-reduced-motion: reduce) { :root { --motion-snap: 0s; } }"
        )
        assert motion_tokens_left_running_under_reduced_motion(stylesheet) == {"--motion-ring"}


LEGAL_NAV = '<nav class="legal"><a href="/a"></a></nav>'


class TestPageInventory:
    def test_lists_a_screen_that_holds_no_legal_nav(self):
        inventory = inventory_of(f'<main data-screen="home">{LEGAL_NAV}</main><section data-screen="ended"></section>')

        assert inventory.screens == {"home", "ended"}
        assert set(inventory.legal_navs_by_screen) == {"home"}

    def test_collects_svg_urls_given_as_xlink_href(self):
        inventory = inventory_of('<svg><use xlink:href="https://x/y#z"/><image xlink:href="/ok.png"/></svg>')

        assert inventory.fetched_urls == ["https://x/y#z", "/ok.png"]

    @pytest.mark.parametrize("void_tag", ["<input />", "<input>", "<source>", "<wbr>", "<br/>"])
    def test_keeps_a_legal_nav_on_its_screen_past_a_void_tag(self, void_tag):
        inventory = inventory_of(
            f'<main data-screen="home">{void_tag}{LEGAL_NAV}</main><div data-screen="call">{LEGAL_NAV}</div>'
        )

        assert set(inventory.legal_navs_by_screen) == {"home", "call"}
