from typing import Protocol

import structlog
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocket
from webauth.dependencies import ConnectionRefusal, authenticate_websocket
from webauth.ports import SessionRecordStore

from hopin.rooms.models import Account

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class IdentityResolver(Protocol):
    async def resolve(self, websocket: WebSocket) -> Account | None:
        """Answer who the socket is, or None to refuse it."""


class SessionIdentityResolver:
    def __init__(self, sessions: SessionRecordStore) -> None:
        self._sessions = sessions

    async def resolve(self, websocket: WebSocket) -> Account | None:
        # The session store is synchronous, so the check runs in a worker.
        user = await run_in_threadpool(authenticate_websocket, websocket, sessions=self._sessions)
        if isinstance(user, ConnectionRefusal):
            await logger.ainfo("handshake_refused", reason=user.value)
            return None
        return Account(id=user.id, name=user.username)
