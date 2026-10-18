from typing import Protocol

import structlog
from starlette.websockets import WebSocket

from hopin.rooms.models import Account

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

ACCOUNT_QUERY_PARAMETER = "id"


class IdentityResolver(Protocol):
    async def resolve(self, websocket: WebSocket) -> Account | None:
        """Answer who the socket is, or None to refuse it."""


class QueryIdIdentityResolver:
    async def resolve(self, websocket: WebSocket) -> Account | None:
        value = websocket.query_params.get(ACCOUNT_QUERY_PARAMETER, "")
        if not value:
            await logger.ainfo("handshake_refused", reason="missing_account")
            return None
        return Account(id=value, name=value)
