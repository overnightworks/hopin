import logging
from enum import StrEnum
from typing import Final, Protocol

from fastapi import APIRouter
from logline import StepTimer
from pydantic import BaseModel, Field

from hopin.config.constants import ICE_SERVERS_PATH, READ_METHODS

_STEPS: Final = logging.getLogger(__name__)


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
    servers: list[IceServer] = []
    with StepTimer("turn_credentials.ended", _STEPS) as step:
        try:
            servers = turn_relay.relay_servers()
        except TurnRelayUnavailableError as unavailable:
            step.refuse(unavailable.reason)
            step.degraded()
    return servers


def create_ice_servers_router(stun_server: str, turn_relay: TurnRelay | None) -> APIRouter:
    router = APIRouter()

    # A sync route runs in FastAPI's threadpool, which the relay's blocking call needs.
    @router.api_route(ICE_SERVERS_PATH, methods=READ_METHODS, response_model_exclude_none=True)
    def ice_servers() -> IceServerList:
        relay_servers = [] if turn_relay is None else _relay_servers_or_none(turn_relay)
        return IceServerList(ice_servers=[IceServer(urls=[stun_server]), *relay_servers])

    return router
