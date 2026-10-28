import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

SESSION_END_TIMEOUT_SECONDS = 10


class ServedSessions:
    """An ASGI wrapper that tells a test when the server has finished a WebSocket session.

    The client sees the close before the server's own session code has run to its end; leaving the client's
    context cancels what is still running, so a test that reads server-side effects waits here first.

    The wait is for the next session end and counts every socket opened through this client, so it must be made
    session-specific before a test that opens several sockets uses it.
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

    def wait_for_the_session_to_end(self) -> None:
        assert self._finished.acquire(timeout=SESSION_END_TIMEOUT_SECONDS), "the server's session did not end"
