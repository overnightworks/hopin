import uvicorn

from hopin.app import create_app
from hopin.config.constants import UVICORN_LOG_LEVEL, UVICORN_WEBSOCKET_IMPLEMENTATION
from hopin.config.settings import ServerSettings


def main() -> None:
    settings = ServerSettings()
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        ws=UVICORN_WEBSOCKET_IMPLEMENTATION,
        access_log=False,
        log_level=UVICORN_LOG_LEVEL,
    )


if __name__ == "__main__":
    main()
