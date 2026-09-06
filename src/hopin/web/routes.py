from pathlib import Path

from aiohttp import web

from hopin.config.constants import CALL_PATH, HEALTH_PATH, JOIN_PATH, WEBSOCKET_PATH
from hopin.rooms.manager import RoomManager
from hopin.signaling.handler import SignalingHandler

STATIC_DIR = Path(__file__).parent / "static"


def create_routes(signaling_handler: SignalingHandler, room_manager: RoomManager) -> list[web.RouteDef]:
    async def health_handler(_request: web.Request) -> web.Response:  # NOSONAR(python:S7503) aiohttp needs coroutines
        return web.json_response({"status": "ok", "rooms": room_manager.room_count})

    async def call_page(_request: web.Request) -> web.Response:  # NOSONAR(python:S7503) aiohttp needs coroutines
        html_path = STATIC_DIR / "call.html"
        return web.Response(text=html_path.read_text(), content_type="text/html")

    async def join_page(request: web.Request) -> web.Response:  # NOSONAR(python:S7503) aiohttp needs coroutines
        room_id = request.match_info.get("room_id", "")
        if not room_manager.has_room(room_id):
            return web.Response(text="Room not found", status=404)
        html_path = STATIC_DIR / "call.html"
        return web.Response(text=html_path.read_text(), content_type="text/html")

    return [
        web.get(HEALTH_PATH, health_handler),
        web.get(CALL_PATH, call_page),
        web.get(f"{JOIN_PATH}/{{room_id}}", join_page),
        web.get(WEBSOCKET_PATH, signaling_handler.handle_websocket),
    ]
