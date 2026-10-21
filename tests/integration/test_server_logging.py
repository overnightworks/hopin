import asyncio
import json
import logging
import secrets
import socket
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from unittest.mock import patch

import pytest
import pytest_asyncio
import uvicorn
import websockets

from hopin.config.constants import AUTH_CALLBACK_PATH, HEALTH_PATH, WEBSOCKET_PATH, SignalType
from hopin.main import main

LOCALHOST = "127.0.0.1"
UVICORN_LOGGER_NAMES = ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi")
ARRIVAL_CAP_SECONDS = 10


@dataclass
class Output:
    port: int
    read: Callable[[], str]


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
    with patch("hopin.main.uvicorn.run") as run:
        main()
    app = run.call_args.args[0]
    listening = socket.socket()
    listening.bind((LOCALHOST, 0))
    listening.listen()
    server = uvicorn.Server(uvicorn.Config(app, **run.call_args.kwargs))
    serving = asyncio.create_task(server.serve(sockets=[listening]))

    def read() -> str:
        captured = capfd.readouterr()
        return captured.out + captured.err

    yield Output(listening.getsockname()[1], read)
    server.should_exit = True
    await serving


async def _send_raw(port: int, request: bytes) -> None:
    reader, writer = await asyncio.open_connection(LOCALHOST, port)
    writer.write(request)
    await writer.drain()
    await asyncio.wait_for(reader.read(), ARRIVAL_CAP_SECONDS)
    writer.close()
    await writer.wait_closed()


async def _get(port: int, target: str) -> None:
    await _send_raw(port, f"GET {target} HTTP/1.1\r\nHost: {LOCALHOST}\r\nConnection: close\r\n\r\n".encode())


async def _join_and_close(port: int, target: str) -> None:
    async with websockets.connect(f"ws://{LOCALHOST}:{port}{target}") as connection:
        await connection.send(json.dumps({"type": SignalType.JOIN.value}))
        await asyncio.wait_for(connection.recv(), ARRIVAL_CAP_SECONDS)


@pytest.mark.integration
class TestServerLogging:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("make_request", "target_template", "path", "sentinel_is_absent"),
        [
            pytest.param(
                _get, f"{AUTH_CALLBACK_PATH}?code={{0}}&state={{0}}", AUTH_CALLBACK_PATH, True, id="sign_in_callback"
            ),
            # structlog's client_connected line prints the account id until #79 finding 14 removes it.
            pytest.param(
                _join_and_close, f"{WEBSOCKET_PATH}?id={{0}}", WEBSOCKET_PATH, False, id="websocket_handshake"
            ),
            pytest.param(_get, f"{HEALTH_PATH}?probe={{0}}", HEALTH_PATH, True, id="health_check"),
        ],
    )
    async def test_request_leaves_no_line_in_the_server_output(
        self, started_server: Output, make_request, target_template: str, path: str, sentinel_is_absent: bool
    ) -> None:
        sentinel = secrets.token_urlsafe(16)
        target = target_template.format(sentinel)

        await make_request(started_server.port, target)

        output = started_server.read()
        assert target not in output
        assert path not in output
        if sentinel_is_absent:
            assert sentinel not in output

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
