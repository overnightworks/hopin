import argparse
from typing import TYPE_CHECKING, TypedDict

import structlog
import uvicorn
from logline import configure
from pydantic import ValidationError

from hopin.agent.feature import configured_game_feature  # game
from hopin.agent.mcp import PLACE_SUBSCRIPTION_WAIT_SECONDS, ParticipationFeature
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
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from fastapi import FastAPI
    from webauth.single_process_stores import SingleProcessStores

    from hopin.stores.shared import SharedStores


logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class UvicornOptions(TypedDict):
    host: str
    port: int
    ws: str
    access_log: bool
    proxy_headers: bool
    log_level: str
    log_config: None


def uvicorn_options(settings: ServerSettings) -> UvicornOptions:
    return UvicornOptions(
        host=settings.host,
        port=settings.port,
        ws=UVICORN_WEBSOCKET_IMPLEMENTATION,
        access_log=False,
        proxy_headers=False,
        log_level=UVICORN_LOG_LEVEL,
        log_config=None,
    )


def command_line_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hopin")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="start the server")
    return parser


def build_application(
    settings: ServerSettings,
    page_build: PageBuild,
    shared_stores: SharedStores,
    *,
    stores: SingleProcessStores | None = None,
    subscription_wait_seconds: float = PLACE_SUBSCRIPTION_WAIT_SECONDS,
) -> FastAPI:
    participation = ParticipationSettings()
    speech = SpeechSettings()
    voices = VoiceStore()
    return create_app(
        settings,
        page_build=page_build,
        shared_stores=shared_stores,
        stores=stores,
        call_features=(
            configured_game_feature(voices),  # game
            ParticipationFeature(
                settings.public_origin,
                participation.agent_adders,
                participation.figure_sources,
                hearing=speech.hearing_service,
                speaking=speech.speaking_service,
                voices=voices,
                redis=shared_stores.async_redis,
                subscription_wait_seconds=subscription_wait_seconds,
            ),
            *configured_skat_feature(settings.public_origin),  # skat
        ),
    )


def run_uvicorn(application: FastAPI, options: UvicornOptions) -> None:
    uvicorn.run(application, **options)


def serve(
    settings: ServerSettings,
    page_build: PageBuild,
    shared_stores: SharedStores,
    *,
    stores: SingleProcessStores | None = None,
    subscription_wait_seconds: float = PLACE_SUBSCRIPTION_WAIT_SECONDS,
    run: Callable[[FastAPI, UvicornOptions], None] = run_uvicorn,
) -> None:
    configure("hopin")
    logger.info("process.started")
    application = build_application(
        settings,
        page_build,
        shared_stores,
        stores=stores,
        subscription_wait_seconds=subscription_wait_seconds,
    )
    run(application, uvicorn_options(settings))


def refusal_lines(invalid: ValidationError) -> str:
    """One line per invalid setting, named by its environment variable; a value never appears."""
    prefix = ServerSettings.model_config["env_prefix"]
    lines = []
    for error in invalid.errors(include_url=False, include_context=False, include_input=False):
        if error["loc"]:
            state = "missing" if error["type"] == "missing" else "malformed"
            lines.append(f"{prefix}{error['loc'][0]}".upper() + f" is {state}")
        else:
            lines.append(error["msg"])
    return "\n".join(lines) + "\n"


def main(arguments: Sequence[str] | None = None, *, build_directory: Path = BUILD_DIRECTORY) -> None:
    parser = command_line_parser()
    parser.parse_args(arguments)
    try:
        settings = ServerSettings()
    except ValidationError as invalid:
        parser.exit(1, refusal_lines(invalid))
    serve(settings, PageBuild.load(build_directory), connect_shared_stores(str(settings.redis_url)))


if __name__ == "__main__":
    main()
