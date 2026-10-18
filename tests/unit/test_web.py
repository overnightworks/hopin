import re
from html.parser import HTMLParser

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hopin.config.constants import CALL_PATH, HEALTH_PATH, JOIN_PATH, STATIC_PATH
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


class TestCallPage:
    def test_returns_html(self, client):
        response = client.get(CALL_PATH)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")


class TestJoinPage:
    def test_returns_html_for_valid_room(self, client, room_manager):
        room = room_manager.create_room("h1", Account(id="h1", name="h1"))
        response = client.get(f"{JOIN_PATH}/{room.room_id}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_returns_404_for_invalid_room(self, client):
        response = client.get(f"{JOIN_PATH}/nonexistent")
        assert response.status_code == 404


class TestStaticAssets:
    @pytest.mark.parametrize(
        ("asset", "content_type"),
        [("css/hopin.css", "text/css"), ("js/app.js", "javascript")],
    )
    def test_serves_asset_with_its_content_type(self, client, asset, content_type):
        response = client.get(f"{STATIC_PATH}/{asset}")
        assert response.status_code == 200
        assert content_type in response.headers["content-type"]

    def test_does_not_serve_the_page_from_the_static_mount(self, client):
        assert client.get(f"{STATIC_PATH}/../index.html").status_code == 404
        assert client.get(f"{STATIC_PATH}/index.html").status_code == 404


class TestHeadRequests:
    @pytest.mark.parametrize("path", [CALL_PATH, HEALTH_PATH, f"{STATIC_PATH}/js/app.js"])
    def test_head_answers_ok(self, client, path):
        assert client.head(path).status_code == 200


SAME_ORIGIN_ABSOLUTE_PATH = re.compile(r"^/[^/]")
URL_ATTRIBUTES = {"src", "href"}


class PageInventory(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.inline_scripts = 0
        self.style_elements = 0
        self.forbidden_attributes: list[str] = []
        self.urls: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "style":
            self.style_elements += 1
        if tag == "script" and not any(name == "src" for name, _ in attrs):
            self.inline_scripts += 1
        for name, value in attrs:
            if name == "style" or name.startswith("on"):
                self.forbidden_attributes.append(name)
            if name in URL_ATTRIBUTES:
                self.urls.append(value)
            if value is not None and value.strip().lower().startswith("javascript:"):
                self.forbidden_attributes.append(f"{name}=javascript:")


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

    def test_references_only_same_origin_absolute_paths(self, page_inventory):
        assert page_inventory.urls
        assert all(SAME_ORIGIN_ABSOLUTE_PATH.match(url) for url in page_inventory.urls)

    def test_loads_the_application_as_a_module(self):
        assert f'<script type="module" src="{STATIC_PATH}/js/app.js">' in PAGE.read_text()
