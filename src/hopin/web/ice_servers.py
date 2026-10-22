from enum import StrEnum
from typing import Protocol

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, Field

from hopin.config.constants import ICE_SERVERS_PATH
from hopin.web.routes import READ_METHODS

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class IceServer(BaseModel):
    urls: list[str]
    username: str | None = None
    credential: str | None = Field(default=None, repr=False)


class IceServerList(BaseModel):
    ice_servers: list[IceServer] = Field(serialization_alias="iceServers")


class TurnRelayFailure(StrEnum):
    UNREACHABLE = "unreachable"
    TIMED_OUT = "timed_out"
    REFUSED = "refused"
    MALFORMED = "malformed"


class TurnRelayUnavailableError(Exception):
    def __init__(self, reason: TurnRelayFailure, status: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


class TurnRelay(Protocol):
    def relay_servers(self) -> list[IceServer]:
        """Fresh relay entries with credentials; raises TurnRelayUnavailableError."""


def _relay_servers_or_none(turn_relay: TurnRelay) -> list[IceServer]:
    try:
        return turn_relay.relay_servers()
    except TurnRelayUnavailableError as unavailable:
        fields: dict[str, str | int] = {"reason": unavailable.reason}
        if unavailable.status is not None:
            fields["status"] = unavailable.status
        logger.warning("turn_relay_unavailable", **fields)
        return []


def create_ice_servers_router(stun_server: str, turn_relay: TurnRelay | None) -> APIRouter:
    router = APIRouter()

    # A sync route runs in FastAPI's threadpool, which the relay's blocking call needs.
    @router.api_route(ICE_SERVERS_PATH, methods=READ_METHODS, response_model_exclude_none=True)
    def ice_servers() -> IceServerList:
        relay_servers = [] if turn_relay is None else _relay_servers_or_none(turn_relay)
        return IceServerList(ice_servers=[IceServer(urls=[stun_server]), *relay_servers])

    return router
