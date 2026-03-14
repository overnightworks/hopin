import pytest
from aiohttp import web

from openstream.config.constants import HEALTH_PATH, STREAM_PATH, VIEWER_PATH
from openstream.rooms.manager import RoomManager
from openstream.signaling.handler import SignalingHandler
from openstream.web.routes import STATIC_DIR, create_routes


@pytest.fixture
def room_manager():
    return RoomManager(max_viewers_per_room=5)


@pytest.fixture
def signaling_handler(room_manager):
    return SignalingHandler(room_manager)


@pytest.fixture
def app(signaling_handler, room_manager):
    application = web.Application()
    routes = create_routes(signaling_handler, room_manager)
    application.router.add_routes(routes)
    return application


class TestCreateRoutes:
    def test_returns_four_routes(self, signaling_handler, room_manager):
        routes = create_routes(signaling_handler, room_manager)
        assert len(routes) == 4

    def test_static_dir_points_to_web_static(self):
        assert STATIC_DIR.name == "static"
        assert STATIC_DIR.parent.name == "web"


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_returns_ok_status(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        response = await client.get(HEALTH_PATH)
        assert response.status == 200
        data = await response.json()
        assert data["status"] == "ok"
        assert data["rooms"] == 0

    @pytest.mark.asyncio
    async def test_returns_room_count(self, aiohttp_client, app, room_manager):
        room_manager.create_room("s1")
        room_manager.create_room("s2")
        client = await aiohttp_client(app)
        response = await client.get(HEALTH_PATH)
        data = await response.json()
        assert data["rooms"] == 2


class TestStreamPage:
    @pytest.mark.asyncio
    async def test_returns_html(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        response = await client.get(STREAM_PATH)
        assert response.status == 200
        assert response.content_type == "text/html"


class TestViewerPage:
    @pytest.mark.asyncio
    async def test_returns_html_for_valid_room(self, aiohttp_client, app, room_manager):
        room = room_manager.create_room("s1")
        client = await aiohttp_client(app)
        response = await client.get(f"{VIEWER_PATH}/{room.room_id}")
        assert response.status == 200
        assert response.content_type == "text/html"

    @pytest.mark.asyncio
    async def test_returns_404_for_invalid_room(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        response = await client.get(f"{VIEWER_PATH}/nonexistent")
        assert response.status == 404
