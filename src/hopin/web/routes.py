from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from hopin.config.constants import (
    CALL_PATH,
    HEALTH_PATH,
    HOME_PATH,
    STATIC_PATH,
    WEBSOCKET_PATH,
)
from hopin.rooms.manager import RoomManager
from hopin.signaling.handler import SignalingHandler

STATIC_DIR = Path(__file__).parent / "static"
PAGE = STATIC_DIR / "index.html"
ASSETS_DIR = STATIC_DIR / "assets"
READ_METHODS = ["GET", "HEAD"]


def create_router(signaling_handler: SignalingHandler, room_manager: RoomManager) -> APIRouter:
    router = APIRouter()

    @router.api_route(HEALTH_PATH, methods=READ_METHODS)
    def health() -> JSONResponse:
        return JSONResponse({"status": "ok", "rooms": room_manager.room_count})

    @router.api_route(HOME_PATH, methods=READ_METHODS)
    def home_page() -> FileResponse:
        return FileResponse(PAGE)

    @router.api_route(f"{CALL_PATH}/{{room_id}}", methods=READ_METHODS)
    def call_page(room_id: str) -> Response:
        if not room_manager.has_room(room_id):
            return PlainTextResponse("Room not found", status_code=404)
        return FileResponse(PAGE)

    router.mount(STATIC_PATH, StaticFiles(directory=ASSETS_DIR), name="assets")
    router.add_api_websocket_route(WEBSOCKET_PATH, signaling_handler.handle_websocket)
    return router
