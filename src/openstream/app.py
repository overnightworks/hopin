import structlog
from aiohttp import web

from openstream.config.settings import ServerSettings
from openstream.rooms.manager import RoomManager
from openstream.signaling.handler import SignalingHandler
from openstream.web.routes import create_routes


def create_app(settings: ServerSettings | None = None) -> web.Application:
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

    app = web.Application()
    routes = create_routes(signaling_handler, room_manager)
    app.router.add_routes(routes)

    app["settings"] = settings

    return app
