import threading
from typing import TYPE_CHECKING

import pytest
from starlette.websockets import WebSocketDisconnect

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send


class ServedSessions:
    """An ASGI wrapper that tells a test when the server has finished a WebSocket session.

    The client sees the close before the server's own session code has run to its end; leaving the client's
    context cancels what is still running, so a test that reads server-side effects waits here first.
    """

    def __init__(self, app: ASGIApp):
        self._app = app
        self._finished = threading.Semaphore(0)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await self._app(scope, receive, send)
        finally:
            if scope["type"] == "websocket":
                self._finished.release()

    def receive_the_close_and_wait_for_the_session_to_end(self, websocket) -> None:
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_json()
        self._finished.acquire()
