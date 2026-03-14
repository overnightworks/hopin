from pathlib import Path

from aiohttp import web

from openstream.config.constants import HEALTH_PATH, STREAM_PATH, VIEWER_PATH, WEBSOCKET_PATH
from openstream.rooms.manager import RoomManager
from openstream.signaling.handler import SignalingHandler

STATIC_DIR = Path(__file__).parent / "static"


def create_routes(signaling_handler: SignalingHandler, room_manager: RoomManager) -> list[web.RouteDef]:
    async def health_handler(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "rooms": room_manager.room_count})

    async def stream_page(_request: web.Request) -> web.Response:
        html_path = STATIC_DIR / "stream.html"
        return web.Response(text=html_path.read_text(), content_type="text/html")

    async def viewer_page(request: web.Request) -> web.Response:
        room_id = request.match_info.get("room_id", "")
        if not room_manager.has_room(room_id):
            return web.Response(text="Room not found", status=404)
        html_path = STATIC_DIR / "viewer.html"
        return web.Response(text=html_path.read_text(), content_type="text/html")

    return [
        web.get(HEALTH_PATH, health_handler),
        web.get(STREAM_PATH, stream_page),
        web.get(f"{VIEWER_PATH}/{{room_id}}", viewer_page),
        web.get(WEBSOCKET_PATH, signaling_handler.handle_websocket),
    ]
