import argparse
from typing import TYPE_CHECKING, TypedDict

import uvicorn

from hopin.agent.feature import configured_game_feature  # game
from hopin.agent.mcp import ParticipationFeature
from hopin.agent.speech_settings import SpeechSettings
from hopin.app import create_app
from hopin.config.constants import UVICORN_LOG_LEVEL, UVICORN_WEBSOCKET_IMPLEMENTATION
from hopin.config.settings import ServerSettings
from hopin.participation.settings import ParticipationSettings
from hopin.skat.feature import configured_skat_feature  # skat
from hopin.web.page_build import BUILD_DIRECTORY, PageBuild

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastapi import FastAPI


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


def build_application(settings: ServerSettings) -> FastAPI:
    participation = ParticipationSettings()
    return create_app(
        settings,
        page_build=PageBuild.load(BUILD_DIRECTORY),
        call_features=(
            configured_game_feature(),  # game
            ParticipationFeature(
                settings.public_origin,
                participation.agent_adders,
                participation.figure_sources,
                hearing=SpeechSettings().hearing_service,
            ),
            *configured_skat_feature(settings.public_origin),  # skat
        ),
    )


def main(arguments: Sequence[str] | None = None) -> None:
    command_line_parser().parse_args(arguments)
    settings = ServerSettings()
    uvicorn.run(build_application(settings), **uvicorn_options(settings))


if __name__ == "__main__":
    main()
