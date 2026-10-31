import re
from pathlib import Path
from xml.etree import ElementTree

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hopin.config.constants import (
    APP_ASSET_PATH,
    CALL_PATH,
    HEALTH_PATH,
    HOME_PATH,
    STATIC_PATH,
)
from hopin.rooms.models import Account
from hopin.signaling.handler import SignalingHandler
from hopin.signaling.scheduler import AsyncioScheduler
from hopin.web.routes import STATIC_DIR, create_router
from tests.support.page_build import fixture_page_build
from tests.support.page_inventory import (
    CSS_COMMENT,
    inventory_of,
)
from tests.support.signaling_fakes import SocketAccountResolver

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
    def test_returns_four_routes_and_the_static_and_app_mounts(self, signaling_handler, room_manager):
        router = create_router(signaling_handler, room_manager, fixture_page_build())
        assert len(router.routes) == 6

    def test_static_dir_points_to_web_static(self):
        assert STATIC_DIR.name == "static"
        assert STATIC_DIR.parent.name == "web"


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

        assert (response.content, response.headers["etag"]) == (page_build.shell.content, page_build.shell.etag)


class TestCallPage:
    def test_returns_html_for_a_known_room(self, client, room_manager):
        room = room_manager.create_room("h1", Account(id="h1", name="h1"))
        response = client.get(f"{CALL_PATH}/{room.room_id}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_answers_the_same_shell_for_a_known_room(self, client, room_manager):
        room = room_manager.create_room("h1", Account(id="h1", name="h1"))

        response = client.get(f"{CALL_PATH}/{room.room_id}")

        assert response.content == fixture_page_build().shell.content

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
            ("css/hopin.css", "text/css"),
        ],
    )
    def test_serves_asset_with_its_content_type(self, client, asset, content_type):
        response = client.get(f"{STATIC_PATH}/{asset}")
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


STYLESHEETS_DIR = STATIC_DIR / "assets" / "css"
REPOSITORY = Path(__file__).parents[2]
# HopIn's own stylesheets: the page's and each one a feature's frontend module loads.
OWN_STYLESHEETS = sorted(
    {
        *(STATIC_DIR / "assets").rglob("*.css"),
        *(REPOSITORY / "frontend" / "src" / "lib").rglob("*.css"),
    }
)
CSS_MOTION_DECLARATION = re.compile(r"(?:transition|animation)(?:-duration)?\s*:([^;}]*)")
CSS_MOTION_TOKEN_REFERENCE = re.compile(r"var\(--motion-[\w-]+\)")
CSS_LITERAL_DURATION = re.compile(r"(?<![\w.-])\d*\.?\d+m?s\b")
CSS_VARIABLE_REFERENCE = re.compile(r"var\(")
CSS_ROOT_MOTION_TOKEN = re.compile(r"(--motion-[\w-]+)\s*:")
CSS_ZEROED_MOTION_TOKEN = re.compile(r"(--motion-[\w-]+)\s*:\s*0s\b")
CSS_REDUCED_MOTION_BLOCK = re.compile(r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?\})\s*\}", re.DOTALL)
CSS_ROOT_BLOCK = re.compile(r":root\s*\{([^{}]*)\}")
FAVICON_FILE = STATIC_DIR / "assets" / "img" / "hopin.svg"


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


class TestPageShell:
    def test_the_icon_is_an_svg_document_in_utf8_with_the_brand_mark_viewbox(self):
        # The file is the repository's own, never untrusted input.
        root = ElementTree.fromstring(FAVICON_FILE.read_bytes().decode("utf-8"))  # noqa: S314 - parses the repository own favicon and no untrusted input
        assert (root.tag, root.get("viewBox")) == ("{http://www.w3.org/2000/svg}svg", "0 0 24 24")

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


class TestPageInventory:
    def test_collects_svg_urls_given_as_xlink_href(self):
        inventory = inventory_of('<svg><use xlink:href="https://x/y#z"/><image xlink:href="/ok.png"/></svg>')

        assert inventory.fetched_urls == ["https://x/y#z", "/ok.png"]
