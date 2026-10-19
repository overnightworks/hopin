import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
import uvicorn
import websockets

from hopin.app import create_app
from hopin.config.constants import UVICORN_WEBSOCKET_IMPLEMENTATION, SignalType
from hopin.config.settings import ServerSettings

LOCALHOST = "127.0.0.1"
CLEAN_CLOSE_CODE = 1000
SETTLE_SECONDS = 0.3


@dataclass
class Call:
    port: int
    host: websockets.ClientConnection
    guest: websockets.ClientConnection
    seat: dict[str, object]

    async def connect(self, account: str) -> websockets.ClientConnection:
        return await websockets.connect(f"ws://{LOCALHOST}:{self.port}/ws?id={account}")

    async def resume_guest_seat(self) -> dict[str, object]:
        frame = {
            "type": SignalType.RESUME.value,
            "participant_id": self.seat["participant_id"],
            "token": self.seat["resume_token"],
            "last_seq": self.seat["seq"],
            "sent_floor": 0,
        }
        return await _say(await self.connect("guest"), frame)

    async def host_hears(self) -> list[str]:
        heard = []
        while True:
            try:
                heard.append(json.loads(await asyncio.wait_for(self.host.recv(), SETTLE_SECONDS))["type"])
            except TimeoutError:
                return heard


async def _say(socket: websockets.ClientConnection, frame: dict[str, object]) -> dict[str, object]:
    await socket.send(json.dumps(frame))
    return json.loads(await socket.recv())


@pytest_asyncio.fixture
async def call() -> AsyncIterator[Call]:
    config = uvicorn.Config(
        create_app(ServerSettings()),
        host=LOCALHOST,
        port=0,
        ws=UVICORN_WEBSOCKET_IMPLEMENTATION,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    # uvicorn exposes no awaitable for "listening"; its started flag is the only signal
    while not server.started:  # noqa: ASYNC110
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    host = await websockets.connect(f"ws://{LOCALHOST}:{port}/ws?id=host")
    room = await _say(host, {"type": SignalType.JOIN.value})
    guest = await websockets.connect(f"ws://{LOCALHOST}:{port}/ws?id=guest")
    seat = await _say(guest, {"type": SignalType.JOIN.value, "room_id": room["room_id"]})
    await host.recv()
    yield Call(port, host, guest, seat)
    server.should_exit = True
    await serving


@pytest.mark.integration
class TestRealTransport:
    @pytest.mark.asyncio
    async def test_aborted_connection_parks_the_seat_and_the_token_resumes_it(self, call: Call) -> None:
        call.guest.transport.abort()

        resumed = await call.resume_guest_seat()

        assert resumed["type"] == SignalType.RESUMED.value
        assert await call.host_hears() == []

    @pytest.mark.asyncio
    async def test_client_closing_with_a_clean_code_leaves_without_parking(self, call: Call) -> None:
        await call.guest.close(CLEAN_CLOSE_CODE)

        assert await call.host_hears() == [SignalType.PARTICIPANT_LEFT.value]
