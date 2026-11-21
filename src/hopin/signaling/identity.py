from typing import TYPE_CHECKING, Protocol

import structlog
from starlette.concurrency import run_in_threadpool
from webauth.dependencies import ConnectionRefusal, authenticate_websocket

from hopin.rooms.models import Account

if TYPE_CHECKING:
    from starlette.websockets import WebSocket
    from webauth.ports import AuthenticatedUser, SessionTransaction

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class IdentityResolver(Protocol):
    async def resolve(self, websocket: WebSocket) -> Account | None:
        """Answer who the socket is, or None to refuse it."""


class SessionIdentityResolver:
    def __init__(self, session_transaction: SessionTransaction) -> None:
        self._session_transaction = session_transaction

    async def resolve(self, websocket: WebSocket) -> Account | None:
        # The session store is synchronous, so the check runs in a worker.
        user = await run_in_threadpool(self._authenticate, websocket)
        if isinstance(user, ConnectionRefusal):
            await logger.ainfo("handshake.refused", reason=user.value)
            return None
        return Account(id=user.id, name=user.username)

    def _authenticate(self, websocket: WebSocket) -> AuthenticatedUser | ConnectionRefusal:
        with self._session_transaction() as sessions:
            return authenticate_websocket(websocket, sessions=sessions)
