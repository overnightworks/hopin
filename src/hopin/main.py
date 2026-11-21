"""The command line: `hopin serve` ends a start it refuses for a known reason in one stderr line and a nonzero exit,
builds the application and runs uvicorn.

Logging. logline is the one logger: `serve` calls `configure("hopin")` once and uvicorn starts with `log_config=None`,
so every line, uvicorn's and the libraries' included, is logline's JSON record with a `trace_id`. logline holds the HTTP
clients' own loggers (`httpx2`, `httpcore2`) at WARNING, which keeps the gateway key out of the log even under DEBUG.

- `create_app` adds logline's `RequestLineMiddleware`, which writes one request line per HTTP request (method, route
  template, status, duration, never the raw path or query) and one `websocket.ended` line per socket session. webauth
  writes none, but sets the line's `sub` once a gate, the sign-in callback or a socket's handshake has admitted a
  person.
- The line's `ip` is the client address that webauth's `ClientAddressMiddleware` records. `create_app` adds it directly
  inside logline's middleware and outside the layers of webauth's installer, so a request that one of them answers (a
  refused path, a rate limit) and a socket's session name the forwarded client too. That middleware is the one place
  `HOPIN_TRUSTED_PROXIES` is read; everything inside reads the recorded address.
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

from hopin.agent.feature import configured_game_feature  # game
from hopin.agent.mcp import PLACE_SUBSCRIPTION_WAIT_SECONDS, ParticipationFeature
from hopin.agent.settings import AgentSettings  # game
from hopin.agent.verdict_check import configured_agreements  # game
from hopin.agent.speech_settings import SpeechSettings
from hopin.app import create_app
from hopin.config.constants import UVICORN_LOG_LEVEL, UVICORN_WEBSOCKET_IMPLEMENTATION
from hopin.config.secret_files import StartRefusedError
from hopin.config.settings import ServerSettings
from hopin.game.verdict_repeatability import passes, printed  # game
from hopin.participation.settings import ParticipationSettings
from hopin.participation.voice import VoiceStore
from hopin.stage.feature import configured_stage_feature  # stage
from hopin.stage.settings import StageSettings  # stage
from hopin.stores.redis import connect_shared_stores
from hopin.web.page_build import BUILD_DIRECTORY, PageBuild

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from fastapi import FastAPI
    from pydantic_settings import BaseSettings
    from webauth.single_process_stores import SingleProcessStores

    from hopin.stores.shared import SharedStores


logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# Pydantic's prefix on a message a model validator raised.
MODEL_VALIDATOR_MESSAGE_PREFIX = "Value error, "

# The field pydantic-settings names when a source cannot parse its value.
SETTINGS_FIELD_IN_MESSAGE = re.compile(r'error parsing value for field "([^"]+)"')

SETTINGS_CLASSES: tuple[type[BaseSettings], ...] = (
    ServerSettings,
    AgentSettings,  # game
    ParticipationSettings,
    SpeechSettings,
    StageSettings,  # stage
)


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
    repeatability = commands.add_parser(
        "verdict-repeatability",
        help="ask the configured quiz master the same recorded reveals again and again and report how often its "
        "verdicts agree; costs real model calls",
    )
    repeatability.add_argument("--runs", type=int, default=10, help="how many times each recorded reveal is asked")
    repeatability.add_argument("--minimum", type=float, default=1.0, help="the least share of runs that must agree")
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
            *configured_stage_feature(settings.public_origin),  # stage
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


def refusal_lines(settings_class: type[BaseSettings], invalid: ValidationError) -> str:
    """One line per invalid setting, named by its environment variable; a value never appears."""
    prefix = settings_class.model_config["env_prefix"]
    delimiter = settings_class.model_config.get("env_nested_delimiter")
    lines = []
    for error in invalid.errors(include_url=False, include_context=False, include_input=False):
        if not error["loc"]:
            lines.append(error["msg"].removeprefix(MODEL_VALIDATOR_MESSAGE_PREFIX))
            continue
        state = "missing" if error["type"] == "missing" else "malformed"
        if delimiter is None:
            name = str(error["loc"][0])
        elif state == "missing" and len(error["loc"]) == 1:
            name = f"{error['loc'][0]}{delimiter}*"
        else:
            name = delimiter.join(str(part) for part in error["loc"])
        lines.append(f"{prefix}{name}".upper() + f" is {state}")
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


def verdict_repeatability(parser: argparse.ArgumentParser, runs: int, minimum: float) -> None:
    """Report the quiz master's agreement with itself; exits 1 below `minimum` or when a run gave no verdict."""
    refusal = refused_settings((AgentSettings,))
    if refusal:
        parser.exit(1, refusal)
    report = asyncio.run(configured_agreements(runs))
    sys.stdout.write(f"{printed(report)}\n")
    parser.exit(0 if passes(report, minimum) else 1)


def main(arguments: Sequence[str] | None = None, *, build_directory: Path = BUILD_DIRECTORY) -> None:
    parser = command_line_parser()
    parsed = parser.parse_args(arguments)
    if parsed.command == "verdict-repeatability":
        verdict_repeatability(parser, parsed.runs, parsed.minimum)
    refusal = refused_settings(SETTINGS_CLASSES)
    if refusal:
        parser.exit(1, refusal)
    try:
        settings = ServerSettings()
        serve(settings, PageBuild.load(build_directory), connect_shared_stores(str(settings.redis_url)))
    except StartRefusedError as refused:
        parser.exit(1, f"{refused}\n")


if __name__ == "__main__":
    main()
