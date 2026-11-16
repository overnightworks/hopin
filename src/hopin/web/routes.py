from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from hopin.config.constants import (
    APP_ASSET_PATH,
    CALL_PATH,
    HEALTH_PATH,
    HOME_PATH,
    IMMUTABLE_ASSET_CACHE_CONTROL,
    IMMUTABLE_ASSET_DIRECTORY,
    READ_METHODS,
    WEBSOCKET_PATH,
)
from hopin.web.page_build import built_page_response

if TYPE_CHECKING:
    import os
    from collections.abc import Callable

    from starlette.types import Scope

    from hopin.signaling.handler import SignalingHandler
    from hopin.web.page_build import PageBuild


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


def create_router(
    signaling_handler: SignalingHandler,
    page_build: PageBuild,
    request_language: Callable[[Request], str],
    stores_answer: Callable[[], bool],
) -> APIRouter:
    router = APIRouter()

    @router.api_route(HEALTH_PATH, methods=READ_METHODS)
    def health() -> JSONResponse:
        if not stores_answer():
            return JSONResponse({"status": "unavailable"}, status_code=HTTPStatus.SERVICE_UNAVAILABLE)
        return JSONResponse({"status": "ok"})

    def shell(request: Request) -> Response:
        return built_page_response(page_build.shell, request_language(request))

    @router.api_route(HOME_PATH, methods=READ_METHODS)
    def home_page(request: Request) -> Response:
        return shell(request)

    # Every call address answers the page; whether the call exists is decided when someone joins.
    @router.api_route(f"{CALL_PATH}/{{room_id}}", methods=READ_METHODS)
    def call_page(request: Request) -> Response:
        return shell(request)

    router.mount(APP_ASSET_PATH, _PageBuildFiles(page_build.app_directory), name="page-build")
    router.add_api_websocket_route(WEBSOCKET_PATH, signaling_handler.handle_websocket)
    return router
