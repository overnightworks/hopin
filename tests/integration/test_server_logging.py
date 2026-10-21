import asyncio
import json
import logging
import secrets
import socket
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from unittest.mock import patch

import pytest
import pytest_asyncio
import uvicorn
import websockets
from webauth.config import installed_web_auth_config

from hopin.app import create_app
from hopin.auth.stores import SignInStores
from hopin.config.constants import AUTH_CALLBACK_PATH, HEALTH_PATH, WEBSOCKET_PATH, SignalType
from hopin.main import main
from tests.signed_in import SeededMember, handshake_headers, seed_member

LOCALHOST = "127.0.0.1"
UVICORN_LOGGER_NAMES = ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi")
ARRIVAL_CAP_SECONDS = 10


@dataclass
class Output:
    port: int
    read: Callable[[], str]
    member: SeededMember


def _logger_state() -> list[tuple[list[logging.Handler], int, bool, bool]]:
    loggers = [logging.getLogger(name) for name in UVICORN_LOGGER_NAMES]
    return [(list(item.handlers), item.level, item.propagate, item.disabled) for item in loggers]


@contextmanager
def preserved_uvicorn_loggers() -> Iterator[None]:
    before = _logger_state()
    try:
        yield
    finally:
        for name, (handlers, level, propagate, disabled) in zip(UVICORN_LOGGER_NAMES, before, strict=True):
            item = logging.getLogger(name)
            item.handlers[:] = handlers
            item.setLevel(level)
            item.propagate = propagate
            item.disabled = disabled


@pytest.fixture
def uvicorn_loggers_restored() -> Iterator[None]:
    with preserved_uvicorn_loggers():
        yield


@pytest_asyncio.fixture
async def started_server(sign_in_settings, monkeypatch, capfd, uvicorn_loggers_restored) -> AsyncIterator[Output]:
    for name, value in sign_in_settings(host=LOCALHOST).environment.items():
        monkeypatch.setenv(name, value)
    stores = SignInStores.in_memory()
    with patch("hopin.main.uvicorn.run") as run, patch("hopin.main.create_app", partial(create_app, stores=stores)):
        main()
    app = run.call_args.args[0]
    member = seed_member(stores, installed_web_auth_config(app))
    listening = socket.socket()
    listening.bind((LOCALHOST, 0))
    listening.listen()
    server = uvicorn.Server(uvicorn.Config(app, **run.call_args.kwargs))
    serving = asyncio.create_task(server.serve(sockets=[listening]))

    def read() -> str:
        captured = capfd.readouterr()
        return captured.out + captured.err

    yield Output(listening.getsockname()[1], read, member)
    server.should_exit = True
    await serving


async def _send_raw(port: int, request: bytes) -> None:
    reader, writer = await asyncio.open_connection(LOCALHOST, port)
    writer.write(request)
    await writer.drain()
    await asyncio.wait_for(reader.read(), ARRIVAL_CAP_SECONDS)
    writer.close()
    await writer.wait_closed()


async def _get(output: Output, target: str) -> None:
    await _send_raw(output.port, f"GET {target} HTTP/1.1\r\nHost: {LOCALHOST}\r\nConnection: close\r\n\r\n".encode())


async def _handshake(output: Output, target: str, *, signed_in: bool) -> None:
    headers = handshake_headers(output.member if signed_in else None)
    async with websockets.connect(f"ws://{LOCALHOST}:{output.port}{target}", additional_headers=headers) as connection:
        await connection.send(json.dumps({"type": SignalType.JOIN.value}))
        await asyncio.wait_for(connection.recv(), ARRIVAL_CAP_SECONDS)


async def _join_and_close(output: Output, target: str) -> None:
    await _handshake(output, target, signed_in=True)


async def _refused_handshake(output: Output, target: str) -> None:
    with pytest.raises(websockets.exceptions.InvalidStatus):
        await _handshake(output, target, signed_in=False)


@pytest.mark.integration
class TestServerLogging:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("make_request", "target_template", "path"),
        [
            pytest.param(
                _get, f"{AUTH_CALLBACK_PATH}?code={{0}}&state={{0}}", AUTH_CALLBACK_PATH, id="sign_in_callback"
            ),
            pytest.param(_join_and_close, f"{WEBSOCKET_PATH}?probe={{0}}", WEBSOCKET_PATH, id="admitted_handshake"),
            pytest.param(_refused_handshake, f"{WEBSOCKET_PATH}?probe={{0}}", WEBSOCKET_PATH, id="refused_handshake"),
            pytest.param(_get, f"{HEALTH_PATH}?probe={{0}}", HEALTH_PATH, id="health_check"),
        ],
    )
    async def test_request_leaves_no_line_in_the_server_output(
        self, started_server: Output, make_request, target_template: str, path: str
    ) -> None:
        sentinel = secrets.token_urlsafe(16)
        target = target_template.format(sentinel)

        await make_request(started_server, target)

        output = started_server.read()
        assert target not in output
        assert path not in output
        assert sentinel not in output
        assert started_server.member.account.username not in output

    @pytest.mark.asyncio
    async def test_invalid_request_still_leaves_a_warning(self, started_server: Output) -> None:
        await _send_raw(started_server.port, b"\x00\x01 not http\r\n\r\n")

        assert "Invalid HTTP request received" in started_server.read()

    def test_logger_state_is_restored_when_the_fixture_ends(self) -> None:
        with preserved_uvicorn_loggers():
            for name in UVICORN_LOGGER_NAMES:
                item = logging.getLogger(name)
                item.setLevel(logging.DEBUG)
                item.propagate = True
                item.disabled = True
            before = _logger_state()
            with preserved_uvicorn_loggers():
                uvicorn.Config(object(), log_level="warning", access_log=False)
                assert _logger_state() != before

            assert _logger_state() == before
