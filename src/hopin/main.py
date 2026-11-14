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
from hopin.participation.voice import VoiceStore
from hopin.skat.feature import configured_skat_feature  # skat
from hopin.stores.redis import connect_shared_stores
from hopin.web.page_build import BUILD_DIRECTORY, PageBuild

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastapi import FastAPI

    from hopin.stores.shared import SharedStores


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


def build_application(settings: ServerSettings, page_build: PageBuild, shared_stores: SharedStores) -> FastAPI:
    participation = ParticipationSettings()
    speech = SpeechSettings()
    voices = VoiceStore()
    return create_app(
        settings,
        page_build=page_build,
        shared_stores=shared_stores,
        call_features=(
            configured_game_feature(voices),  # game
            ParticipationFeature(
                settings.public_origin,
                participation.agent_adders,
                participation.figure_sources,
                hearing=speech.hearing_service,
                speaking=speech.speaking_service,
                voices=voices,
            ),
            *configured_skat_feature(settings.public_origin),  # skat
        ),
    )


def serve(settings: ServerSettings, page_build: PageBuild) -> None:
    application = build_application(settings, page_build, connect_shared_stores(str(settings.redis_url)))
    uvicorn.run(application, **uvicorn_options(settings))


def main(arguments: Sequence[str] | None = None) -> None:
    command_line_parser().parse_args(arguments)
    serve(ServerSettings(), PageBuild.load(BUILD_DIRECTORY))


if __name__ == "__main__":
    main()
