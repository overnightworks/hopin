import hashlib
import re
from html.parser import HTMLParser

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hopin.config.constants import CALL_PATH, HEALTH_PATH, HOME_PATH, STATIC_PATH
from hopin.rooms.manager import RoomManager
from hopin.rooms.models import Account
from hopin.signaling.handler import SignalingHandler
from hopin.signaling.identity import QueryIdIdentityResolver
from hopin.signaling.scheduler import AsyncioScheduler
from hopin.web.routes import PAGE, STATIC_DIR, create_router


@pytest.fixture
def room_manager():
    return RoomManager(max_participants_per_room=5)


@pytest.fixture
def signaling_handler(room_manager):
    return SignalingHandler(room_manager, QueryIdIdentityResolver(), AsyncioScheduler())


@pytest.fixture
def app(signaling_handler, room_manager):
    application = FastAPI()
    application.include_router(create_router(signaling_handler, room_manager))
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


class TestCreateRoutes:
    def test_returns_four_routes_and_the_static_mount(self, signaling_handler, room_manager):
        router = create_router(signaling_handler, room_manager)
        assert len(router.routes) == 5

    def test_static_dir_points_to_web_static(self):
        assert STATIC_DIR.name == "static"
        assert STATIC_DIR.parent.name == "web"


class TestHealthEndpoint:
    def test_returns_ok_status(self, client):
        response = client.get(HEALTH_PATH)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["rooms"] == 0

    def test_returns_room_count(self, client, room_manager):
        room_manager.create_room("h1", Account(id="h1", name="h1"))
        room_manager.create_room("h2", Account(id="h2", name="h2"))
        response = client.get(HEALTH_PATH)
        data = response.json()
        assert data["rooms"] == 2


class TestHomePage:
    def test_returns_html(self, client):
        response = client.get(HOME_PATH)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")


class TestCallPage:
    def test_returns_html_for_a_known_room(self, client, room_manager):
        room = room_manager.create_room("h1", Account(id="h1", name="h1"))
        response = client.get(f"{CALL_PATH}/{room.room_id}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

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
            ("js/app.js", "javascript"),
            ("js/router.js", "javascript"),
            ("js/home.js", "javascript"),
        ],
    )
    def test_serves_asset_with_its_content_type(self, client, asset, content_type):
        response = client.get(f"{STATIC_PATH}/{asset}")
        assert response.status_code == 200
        assert content_type in response.headers["content-type"]

    def test_does_not_serve_the_page_from_the_static_mount(self, client):
        assert client.get(f"{STATIC_PATH}/index.html").status_code == 404


class TestHeadRequests:
    @pytest.mark.parametrize("path", [HOME_PATH, HEALTH_PATH, f"{STATIC_PATH}/js/app.js"])
    def test_head_answers_ok(self, client, path):
        assert client.head(path).status_code == 200


SAME_ORIGIN_ABSOLUTE_PATH = re.compile(r"^/[^/]")
LEGAL_URLS = ("https://hallucinai.de/impressum", "https://hallucinai.de/datenschutz#hopin")
NEW_TAB_LINK_ATTRIBUTES = {"target": "_blank", "rel": "noopener"}
UI_CONSOLE_SHA256 = "abcc85ad2171201d71002fc497a16b2da38caac1b9fa48a059a26637e6b4da01"
STYLESHEETS_DIR = STATIC_DIR / "assets" / "css"
UI_CONSOLE_CSS = STYLESHEETS_DIR / "ui-console.css"
CSS_CLASS_NAME = re.compile(r"\.(-?[_a-zA-Z][\w-]*)")
CSS_DECLARATION_BLOCK = re.compile(r"\{[^{}]*\}")
CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
CSS_MOTION_DECLARATION = re.compile(r"(?:transition|animation)(?:-duration)?\s*:([^;}]*)")
CSS_MOTION_TOKEN_REFERENCE = re.compile(r"var\(--motion-[\w-]+\)")
CSS_LITERAL_DURATION = re.compile(r"(?<![\w.-])\d*\.?\d+m?s\b")
CSS_VARIABLE_REFERENCE = re.compile(r"var\(")
CSS_ROOT_MOTION_TOKEN = re.compile(r"(--motion-[\w-]+)\s*:")
CSS_ZEROED_MOTION_TOKEN = re.compile(r"(--motion-[\w-]+)\s*:\s*0s\b")
CSS_REDUCED_MOTION_BLOCK = re.compile(r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?\})\s*\}", re.DOTALL)
CSS_ROOT_BLOCK = re.compile(r":root\s*\{([^{}]*)\}")
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
URL_ATTRIBUTES = {"src", "href", "xlink:href"}


class PageInventory(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.inline_scripts = 0
        self.style_elements = 0
        self.forbidden_attributes: list[str] = []
        self.fetched_urls: list[str] = []
        self.links: list[dict[str, str | None]] = []
        self.stylesheets: list[str] = []
        self.class_values: list[str] = []
        self.screens: set[str] = set()
        self.legal_navs_by_screen: dict[str, list[list[str]]] = {}
        self._screens: list[tuple[str, int]] = []
        self._legal_nav_links: list[str] | None = None
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag not in VOID_TAGS:
            self._depth += 1
        attributes = dict(attrs)
        self._track_screen(attributes)
        self._track_legal_nav(tag, attributes)
        self._inspect(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._inspect(tag, attrs)

    def _inspect(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "style":
            self.style_elements += 1
        if tag == "script" and "src" not in attributes:
            self.inline_scripts += 1
        if tag == "link" and attributes.get("rel") == "stylesheet":
            self.stylesheets.append(attributes["href"])
        if "class" in attributes:
            self.class_values.append(attributes["class"])
        if tag == "a":
            self.links.append(attributes)
            if self._legal_nav_links is not None:
                self._legal_nav_links.append(attributes["href"])
        for name, value in attrs:
            if name == "style" or name.startswith("on"):
                self.forbidden_attributes.append(name)
            if name in URL_ATTRIBUTES and tag != "a":
                self.fetched_urls.append(value)
            if value is not None and value.strip().lower().startswith("javascript:"):
                self.forbidden_attributes.append(f"{name}=javascript:")

    def handle_endtag(self, tag):
        if tag == "nav" and self._legal_nav_links is not None:
            self._record_legal_nav()
        while self._screens and self._screens[-1][1] == self._depth:
            self._screens.pop()
        self._depth -= 1

    def _track_screen(self, attributes):
        if "data-screen" in attributes:
            self.screens.add(attributes["data-screen"])
            self._screens.append((attributes["data-screen"], self._depth))

    def _track_legal_nav(self, tag, attributes):
        if tag == "nav" and attributes.get("class") == "legal":
            self._legal_nav_links = []

    def _record_legal_nav(self):
        screen = self._screens[-1][0] if self._screens else ""
        self.legal_navs_by_screen.setdefault(screen, []).append(self._legal_nav_links)
        self._legal_nav_links = None


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


def defined_class_names(stylesheet: str) -> set[str]:
    selectors = CSS_DECLARATION_BLOCK.sub("{}", CSS_COMMENT.sub("", stylesheet))
    return set(CSS_CLASS_NAME.findall(selectors))


@pytest.fixture
def page_inventory():
    inventory = PageInventory()
    inventory.feed(PAGE.read_text())
    return inventory


class TestPageIsCspReady:
    def test_has_no_inline_script_style_or_handler(self, page_inventory):
        assert page_inventory.inline_scripts == 0
        assert page_inventory.style_elements == 0
        assert page_inventory.forbidden_attributes == []

    def test_fetches_only_same_origin_absolute_paths(self, page_inventory):
        assert page_inventory.fetched_urls
        assert all(SAME_ORIGIN_ABSOLUTE_PATH.match(url) for url in page_inventory.fetched_urls)

    def test_links_only_to_the_two_central_legal_pages_in_a_new_tab(self, page_inventory):
        assert {link["href"] for link in page_inventory.links} == set(LEGAL_URLS)
        assert all(
            link[name] == value for link in page_inventory.links for name, value in NEW_TAB_LINK_ATTRIBUTES.items()
        )

    def test_loads_the_application_as_a_module(self):
        assert f'<script type="module" src="{STATIC_PATH}/js/app.js">' in PAGE.read_text()


class TestPageShell:
    def test_loads_ui_console_before_hopin(self, page_inventory):
        assert page_inventory.stylesheets == [f"{STATIC_PATH}/css/ui-console.css", f"{STATIC_PATH}/css/hopin.css"]

    def test_vendored_ui_console_is_byte_identical_to_its_release(self):
        assert hashlib.sha256(UI_CONSOLE_CSS.read_bytes()).hexdigest() == UI_CONSOLE_SHA256

    def test_reuses_no_class_name_that_ui_console_defines(self, page_inventory):
        vendored = defined_class_names(UI_CONSOLE_CSS.read_text())
        own_stylesheet = defined_class_names((STYLESHEETS_DIR / "hopin.css").read_text())
        used_in_markup = {name for value in page_inventory.class_values for name in value.split()}
        assert vendored
        assert vendored & (own_stylesheet | used_in_markup) == set()

    def test_gives_every_transition_and_animation_its_duration_from_a_motion_token(self):
        stylesheet = (STYLESHEETS_DIR / "hopin.css").read_text()
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

    def test_every_screen_holds_exactly_one_legal_nav_with_both_links(self, page_inventory):
        assert {"home", "call"} <= page_inventory.screens
        assert set(page_inventory.legal_navs_by_screen) == page_inventory.screens
        for navs in page_inventory.legal_navs_by_screen.values():
            assert navs == [list(LEGAL_URLS)]


LEGAL_NAV = '<nav class="legal"><a href="/a"></a></nav>'


def inventory_of(markup: str) -> PageInventory:
    inventory = PageInventory()
    inventory.feed(markup)
    return inventory


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
