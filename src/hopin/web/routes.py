from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from hopin.config.constants import (
    APP_ASSET_PATH,
    CALL_PATH,
    HEALTH_PATH,
    HOME_PATH,
    IMMUTABLE_ASSET_CACHE_CONTROL,
    IMMUTABLE_ASSET_DIRECTORY,
    STATIC_PATH,
    WEBSOCKET_PATH,
)
from hopin.web.kit import KIT_DIR, KIT_PATH

if TYPE_CHECKING:
    import os

    from starlette.types import Scope

    from hopin.rooms.manager import RoomManager
    from hopin.signaling.handler import SignalingHandler
    from hopin.web.page_build import BuiltPage, PageBuild

STATIC_DIR = Path(__file__).parent / "static"
ASSETS_DIR = STATIC_DIR / "assets"
READ_METHODS = ["GET", "HEAD"]


def built_page_response(page: BuiltPage, status_code: int = 200) -> Response:
    return Response(page.content, status_code=status_code, media_type="text/html", headers={"ETag": page.etag})


class _PageBuildFiles(StaticFiles):
    """The page build's files, with the immutable ones marked so for the browser, a 304 included."""

    def __init__(self, directory: Path) -> None:
        super().__init__(directory=directory)
        self._immutable_directory = (directory / IMMUTABLE_ASSET_DIRECTORY).resolve()

    def file_response(
        self, full_path: str | os.PathLike[str], stat_result: os.stat_result, scope: Scope, status_code: int = 200
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        if Path(full_path).is_relative_to(self._immutable_directory):
            response.headers["Cache-Control"] = IMMUTABLE_ASSET_CACHE_CONTROL
        return response


def create_router(signaling_handler: SignalingHandler, room_manager: RoomManager, page_build: PageBuild) -> APIRouter:
    router = APIRouter()

    @router.api_route(HEALTH_PATH, methods=READ_METHODS)
    def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    def shell() -> Response:
        return built_page_response(page_build.shell)

    @router.api_route(HOME_PATH, methods=READ_METHODS)
    def home_page() -> Response:
        return shell()

    @router.api_route(f"{CALL_PATH}/{{room_id}}", methods=READ_METHODS)
    def call_page(room_id: str) -> Response:
        if not room_manager.has_room(room_id):
            return PlainTextResponse("Room not found", status_code=404)
        return shell()

    router.mount(KIT_PATH, StaticFiles(directory=KIT_DIR), name="appkit")
    router.mount(STATIC_PATH, StaticFiles(directory=ASSETS_DIR), name="assets")
    router.mount(APP_ASSET_PATH, _PageBuildFiles(page_build.app_directory), name="page-build")
    router.add_api_websocket_route(WEBSOCKET_PATH, signaling_handler.handle_websocket)
    return router
