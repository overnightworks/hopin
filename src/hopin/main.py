from typing import TypedDict

import uvicorn

from hopin.app import create_app
from hopin.config.constants import UVICORN_LOG_LEVEL, UVICORN_WEBSOCKET_IMPLEMENTATION
from hopin.config.settings import ServerSettings


class UvicornOptions(TypedDict):
    host: str
    port: int
    ws: str
    access_log: bool
    log_level: str


def uvicorn_options(settings: ServerSettings) -> UvicornOptions:
    return UvicornOptions(
        host=settings.host,
        port=settings.port,
        ws=UVICORN_WEBSOCKET_IMPLEMENTATION,
        access_log=False,
        log_level=UVICORN_LOG_LEVEL,
    )


def main() -> None:
    settings = ServerSettings()
    uvicorn.run(create_app(settings), **uvicorn_options(settings))


if __name__ == "__main__":
    main()
