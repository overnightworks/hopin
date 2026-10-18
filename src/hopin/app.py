import structlog
from fastapi import FastAPI

from hopin.config.settings import ServerSettings
from hopin.rooms.manager import RoomManager
from hopin.signaling.handler import SignalingHandler
from hopin.web.ice_servers import create_ice_servers_router
from hopin.web.routes import create_router


def create_app(settings: ServerSettings | None = None) -> FastAPI:
    if settings is None:
        settings = ServerSettings()

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
    )

    room_manager = RoomManager(max_participants_per_room=settings.max_participants_per_room)
    signaling_handler = SignalingHandler(room_manager)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(create_router(signaling_handler, room_manager))
    app.include_router(create_ice_servers_router(settings.stun_server))
    app.state.settings = settings

    return app
