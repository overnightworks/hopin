"""The command line: `hopin serve` ends a start it refuses for a known reason in one stderr line and a nonzero exit,
builds the application and runs uvicorn; `hopin migrate` brings the database to the schema of this image and reads
only the database settings.

Logging. logline is the one logger: `serve` calls `configure("hopin")` once and uvicorn starts with `log_config=None`,
so every line, uvicorn's and the libraries' included, is logline's JSON record with a `trace_id`. logline holds the HTTP
clients' own loggers (`httpx2`, `httpcore2`) at WARNING, which keeps the gateway key out of the log even under DEBUG.

- `create_app` builds webauth with `front=TrustedProxies`, so webauth's `install` places logline's
  `RequestLineMiddleware` outermost: one request line per HTTP request (method, route template, status, duration, never
  the raw path or query) and one `websocket.ended` line per socket session. webauth sets the line's `sub` once a gate,
  the sign-in callback or a socket's handshake has admitted a person, and writes the sign-in callback's
  `sign_in.refused` and `sign_in.completed`.
- The line's `ip` is the client address that webauth's client-address writer records inside the request line and outside
  the rest of the stack, so a request that one of its layers answers (a refused path, a rate limit) and a socket's
  session name the forwarded client too. `front` is the one place `HOPIN_TRUSTED_PROXIES` is read; everything inside
  reads the recorded address.
- Every event is a literal `<noun>.<verb_past>` (logline's fleet standard). A line that closes a timed step ends in
  `.ended` and carries `duration_ms` and `outcome` from logline's `StepTimer`; a step that ends because the service gave
  no answer ends `refused` with its closed `reason`, and `failed` is an exception, which the timer names by its class.
  Other lines are state changes, refusals with a `reason` and errors with an `error_type`, each written once where it
  stops.
- A WebSocket session runs in a trace of its own from the upgrade's `traceparent`, else a new one, and its
  `websocket.ended` and the lines outside a turn carry it. A turn on a WebSocket, a signaling frame and a socket's
  disconnect step run inside logline's `unit_trace`, which detaches at its end; a timer the scheduler starts runs in a
  trace of its own, not the one of the frame that scheduled it.
- The fields are ids, counts and durations with their unit in the name, and codes from closed sets (peer ids, event
  kinds, causes and reasons, close codes, the call cap). No line carries the room id, which is the invite link's token:
  `room_ref` is a keyed digest of it (`rooms/reference.py`). No event has a field for chat text, a quiz answer, tokens
  or secrets.
"""

import argparse
import asyncio
import re
import sys
from typing import TYPE_CHECKING, TypedDict

import structlog
import uvicorn
from logline import configure
from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from hopin.agent.backend import gateway_backend  # game
from hopin.agent.feature import configured_game_feature  # game
from hopin.agent.fleet_keys import FleetKeyedSettings
from hopin.agent.mcp import PLACE_SUBSCRIPTION_WAIT_SECONDS, ParticipationFeature
from hopin.agent.settings import AgentSettings  # game
from hopin.agent.speech_settings import SpeechSettings
from hopin.agent.verdict_check import BackendOf, add_command, configured_agreements  # game
from hopin.agent.video_settings import VideoSettings
from hopin.app import create_app
from hopin.auth.sign_in import LoggingAuditSink
from hopin.config.constants import UVICORN_WEBSOCKET_IMPLEMENTATION
from hopin.config.secret_files import StartRefusedError
from hopin.config.settings import DatabaseSettings, RealmSettings, ServerSettings
from hopin.game.verdict_repeatability import passes, printed  # game
from hopin.participation.settings import ParticipationSettings
from hopin.participation.voice import VoiceStore
from hopin.stage.feature import configured_stage_feature  # stage
from hopin.stage.settings import StageSettings  # stage
from hopin.stores.postgres import (
    PostgresIdentity,
    create_database_engine,
    migrate,
    require_head,
    with_database_readiness,
)
from hopin.stores.redis import connect_shared_stores
from hopin.web.page_build import BUILD_DIRECTORY, PageBuild

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from fastapi import FastAPI
    from pydantic_settings import BaseSettings

    from hopin.stores.identity import IdentityStores
    from hopin.stores.shared import SharedStores


logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# Pydantic's prefix on a message a model validator raised.
MODEL_VALIDATOR_MESSAGE_PREFIX = "Value error, "

# The field pydantic-settings names when a source cannot parse its value.
SETTINGS_FIELD_IN_MESSAGE = re.compile(r'error parsing value for field "([^"]+)"')

SETTINGS_CLASSES: tuple[type[BaseSettings], ...] = (
    RealmSettings,
    ServerSettings,
    AgentSettings,  # game
    ParticipationSettings,
    SpeechSettings,
    VideoSettings,
    StageSettings,  # stage
)


class UvicornOptions(TypedDict):
    host: str
    port: int
    ws: str
    access_log: bool
    proxy_headers: bool


def uvicorn_options(settings: ServerSettings) -> UvicornOptions:
    return UvicornOptions(
        host=settings.host,
        port=settings.port,
        ws=UVICORN_WEBSOCKET_IMPLEMENTATION,
        access_log=False,
        proxy_headers=False,
    )


def command_line_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hopin")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="start the server; it refuses a database that is not at this image's schema")
    commands.add_parser("migrate", help="bring the database to this image's schema")
    add_command(commands)  # game
    return parser


def build_application(
    settings: ServerSettings,
    realm: RealmSettings,
    page_build: PageBuild,
    shared_stores: SharedStores,
    identity: IdentityStores,
    *,
    subscription_wait_seconds: float = PLACE_SUBSCRIPTION_WAIT_SECONDS,
) -> FastAPI:
    participation = ParticipationSettings()
    speech = SpeechSettings()
    video = VideoSettings()
    voices = VoiceStore()
    return create_app(
        settings,
        realm,
        page_build=page_build,
        shared_stores=shared_stores,
        identity=identity,
        call_features=(
            configured_game_feature(voices),  # game
            ParticipationFeature(
                settings.public_origin,
                participation.figure_sources,
                hearing=speech.hearing_service,
                speaking=speech.speaking_service,
                video=video.service,
                voices=voices,
                redis=shared_stores.async_redis,
                realm=realm,
                subscription_wait_seconds=subscription_wait_seconds,
            ),
            *configured_stage_feature(settings.public_origin),  # stage
        ),
    )


def run_uvicorn(application: FastAPI, options: UvicornOptions) -> None:
    uvicorn.run(
        application,
        host=options["host"],
        port=options["port"],
        ws=options["ws"],
        access_log=options["access_log"],
        proxy_headers=options["proxy_headers"],
        log_config=None,
    )


def serve(
    settings: ServerSettings,
    realm: RealmSettings,
    page_build: PageBuild,
    shared_stores: SharedStores,
    identity: IdentityStores,
    *,
    subscription_wait_seconds: float = PLACE_SUBSCRIPTION_WAIT_SECONDS,
    run: Callable[[FastAPI, UvicornOptions], None] = run_uvicorn,
) -> None:
    configure("hopin")
    logger.info("process.started")
    application = build_application(
        settings,
        realm,
        page_build,
        shared_stores,
        identity,
        subscription_wait_seconds=subscription_wait_seconds,
    )
    run(application, uvicorn_options(settings))


def serve_on_migrated_database(
    settings: ServerSettings,
    realm: RealmSettings,
    page_build: PageBuild,
    *,
    subscription_wait_seconds: float = PLACE_SUBSCRIPTION_WAIT_SECONDS,
    run: Callable[[FastAPI, UvicornOptions], None] = run_uvicorn,
) -> None:
    """Production's wiring of `serve`: the shared Redis and a database at this image's head, which is checked after the
    settings have built the application; the engine ends with the server."""
    engine = create_database_engine(settings)

    def run_at_head(application: FastAPI, options: UvicornOptions) -> None:
        require_head(engine)
        run(application, options)

    try:
        shared_stores = with_database_readiness(connect_shared_stores(str(settings.redis_url)), engine)
        serve(
            settings,
            realm,
            page_build,
            shared_stores,
            PostgresIdentity(engine, LoggingAuditSink()),
            subscription_wait_seconds=subscription_wait_seconds,
            run=run_at_head,
        )
    finally:
        engine.dispose()


def migrate_database(settings: DatabaseSettings) -> None:
    configure("hopin")
    engine = create_database_engine(settings)
    try:
        migrate(engine)
    finally:
        engine.dispose()


def refusal_lines(settings_class: type[BaseSettings], invalid: ValidationError) -> str:
    """One line per invalid setting, named by its environment variable; a value never appears."""
    prefix = settings_class.model_config["env_prefix"]
    delimiter = settings_class.model_config.get("env_nested_delimiter")
    environment_keys = settings_class.environment_keys if issubclass(settings_class, FleetKeyedSettings) else {}
    lines = []
    for error in invalid.errors(include_url=False, include_context=False, include_input=False):
        if not error["loc"]:
            lines.append(error["msg"].removeprefix(MODEL_VALIDATOR_MESSAGE_PREFIX))
            continue
        state = "missing" if error["type"] == "missing" else "malformed"
        location = tuple(str(part) for part in error["loc"])
        if (key := environment_keys.get(location)) is not None:
            lines.append(f"{key} is {state}")
            continue
        if delimiter is None:
            name = str(error["loc"][0])
        elif state == "missing" and len(error["loc"]) == 1:
            name = f"{error['loc'][0]}{delimiter}*"
        else:
            name = delimiter.join(str(part) for part in error["loc"])
        lines.append(f"{prefix}{name}".upper() + f" is {state}")
        if state == "missing":
            lines.extend(
                f"{key} is missing" for mapped, key in environment_keys.items() if mapped[: len(location)] == location
            )
    return "\n".join(lines) + "\n"


def unreadable_line(settings_class: type[BaseSettings], unreadable: SettingsError) -> str:
    """The line for a value its source cannot parse; the message holds the field, never the value."""
    (field,) = SETTINGS_FIELD_IN_MESSAGE.findall(str(unreadable))
    return f"{settings_class.model_config['env_prefix']}{field}".upper() + " is malformed\n"


def refused_settings(settings_classes: Sequence[type[BaseSettings]]) -> str:
    """The refusal of every settings class the server composes, empty when each reads."""
    refusal = ""
    for settings_class in settings_classes:
        try:
            settings_class()
        except ValidationError as invalid:
            refusal += refusal_lines(settings_class, invalid)
        except SettingsError as unreadable:
            refusal += unreadable_line(settings_class, unreadable)
    return refusal


def main(
    arguments: Sequence[str] | None = None,
    *,
    build_directory: Path = BUILD_DIRECTORY,
    quiz_backend_of: BackendOf = gateway_backend,  # game
) -> None:
    parser = command_line_parser()
    parsed = parser.parse_args(arguments)
    if parsed.command == "verdict-repeatability":  # game
        refusal = refused_settings((AgentSettings,))  # game
        if refusal:  # game
            parser.exit(1, refusal)  # game
        report = asyncio.run(configured_agreements(parsed.runs, quiz_backend_of))  # game
        sys.stdout.write(f"{printed(report)}\n")  # game
        parser.exit(0 if passes(report, parsed.minimum) else 1)  # game
    refusal = refused_settings((DatabaseSettings,) if parsed.command == "migrate" else SETTINGS_CLASSES)
    if refusal:
        parser.exit(1, refusal)
    try:
        if parsed.command == "migrate":
            migrate_database(DatabaseSettings())
        else:
            serve_on_migrated_database(ServerSettings(), RealmSettings(), PageBuild.load(build_directory))
    except StartRefusedError as refused:
        parser.exit(1, f"{refused}\n")


if __name__ == "__main__":
    main()
