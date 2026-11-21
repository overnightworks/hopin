from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from hopin.config.constants import CALL_PATH, READ_METHODS, START_LINK_PATH
from hopin.stage.settings import named_context
from hopin.web.page_build import built_page_response

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from hopin.stage.settings import LinkedContext, StageApp
    from hopin.web.page_build import PageBuild

APP_QUERY = "app"
CONTEXT_QUERY = "context"


def _table_of(apps: Sequence[StageApp], request: Request) -> LinkedContext | None:
    """The table the query names: one app and one key, each given once, of a registered app."""
    names = request.query_params.getlist(APP_QUERY)
    contexts = request.query_params.getlist(CONTEXT_QUERY)
    if len(names) != 1 or len(contexts) != 1:
        return None
    return named_context(apps, names[0], contexts[0])


def create_start_link_router(
    apps: Sequence[StageApp],
    room_showing: Callable[[str, str], str | None],
    page_build: PageBuild,
    request_language: Callable[[Request], str],
) -> APIRouter:
    """`GET /c/new?app=<name>&context=<key>`, behind the session gate: the one open call that shows the table, else the
    page, which starts a call and attaches the table to it. A link naming no registered table is a plain 404."""
    router = APIRouter()

    @router.api_route(START_LINK_PATH, methods=READ_METHODS)
    def start_link(request: Request) -> Response:
        table = _table_of(apps, request)
        if table is None:
            raise HTTPException(status_code=404)
        room_id = room_showing(table.app.name, table.context)
        if room_id is not None:
            return RedirectResponse(f"{CALL_PATH}/{room_id}", status_code=303)
        return built_page_response(page_build.shell, request_language(request))

    return router
