import argparse
from typing import TYPE_CHECKING, TypedDict

import uvicorn

from hopin.app import create_app
from hopin.config.constants import UVICORN_LOG_LEVEL, UVICORN_WEBSOCKET_IMPLEMENTATION
from hopin.config.settings import ServerSettings
from hopin.game import GameSettings, game_feature  # game
from hopin.web.page_build import BUILD_DIRECTORY, PageBuild

if TYPE_CHECKING:
    from collections.abc import Sequence


class UvicornOptions(TypedDict):
    host: str
    port: int
    ws: str
    access_log: bool
    proxy_headers: bool
    log_level: str


def uvicorn_options(settings: ServerSettings) -> UvicornOptions:
    return UvicornOptions(
        host=settings.host,
        port=settings.port,
        ws=UVICORN_WEBSOCKET_IMPLEMENTATION,
        access_log=False,
        proxy_headers=False,
        log_level=UVICORN_LOG_LEVEL,
    )


def command_line_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hopin")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="start the server")
    return parser


def main(arguments: Sequence[str] | None = None) -> None:
    command_line_parser().parse_args(arguments)
    settings = ServerSettings()
    app = create_app(
        settings,
        page_build=PageBuild.load(BUILD_DIRECTORY),
        call_feature=game_feature(GameSettings()),  # game
    )
    uvicorn.run(app, **uvicorn_options(settings))


if __name__ == "__main__":
    main()
