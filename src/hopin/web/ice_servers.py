from fastapi import APIRouter
from pydantic import BaseModel, Field

from hopin.config.constants import ICE_SERVERS_PATH
from hopin.web.routes import READ_METHODS


class IceServer(BaseModel):
    urls: list[str]


class IceServerList(BaseModel):
    ice_servers: list[IceServer] = Field(serialization_alias="iceServers")


def create_ice_servers_router(stun_server: str) -> APIRouter:
    router = APIRouter()

    @router.api_route(ICE_SERVERS_PATH, methods=READ_METHODS)
    def ice_servers() -> IceServerList:
        return IceServerList(ice_servers=[IceServer(urls=[stun_server])])

    return router
