import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hopin.config.constants import CALL_PATH, HEALTH_PATH, JOIN_PATH
from hopin.rooms.manager import RoomManager
from hopin.signaling.handler import SignalingHandler
from hopin.web.routes import STATIC_DIR, create_router


@pytest.fixture
def room_manager():
    return RoomManager(max_participants_per_room=5)


@pytest.fixture
def signaling_handler(room_manager):
    return SignalingHandler(room_manager)


@pytest.fixture
def app(signaling_handler, room_manager):
    application = FastAPI()
    application.include_router(create_router(signaling_handler, room_manager))
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


class TestCreateRoutes:
    def test_returns_four_routes(self, signaling_handler, room_manager):
        router = create_router(signaling_handler, room_manager)
        assert len(router.routes) == 4

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
        room_manager.create_room("h1")
        room_manager.create_room("h2")
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
        room = room_manager.create_room("h1")
        response = client.get(f"{JOIN_PATH}/{room.room_id}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_returns_404_for_invalid_room(self, client):
        response = client.get(f"{JOIN_PATH}/nonexistent")
        assert response.status_code == 404


class TestHeadRequests:
    @pytest.mark.parametrize("path", [CALL_PATH, HEALTH_PATH])
    def test_head_answers_ok(self, client, path):
        assert client.head(path).status_code == 200
