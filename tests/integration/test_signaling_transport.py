import asyncio
import json
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import uvicorn
import websockets
from webauth.config import installed_web_auth_config

from hopin.app import create_app
from hopin.auth.stores import SignInStores
from hopin.config.constants import UVICORN_WEBSOCKET_IMPLEMENTATION, SignalType
from hopin.signaling.frames import SEQUENCE_KEY
from tests.signed_in import SeededMember, handshake_headers, seed_member

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

LOCALHOST = "127.0.0.1"
CLEAN_CLOSE_CODE = 1000
ARRIVAL_CAP_SECONDS = 10
BARRIER_MESSAGE = "after the resume"
FIRST_CLIENT_SEQUENCE = 1
FORBIDDEN_STATUS = 403


@dataclass
class Call:
    port: int
    host: websockets.ClientConnection
    guest: websockets.ClientConnection
    seat: dict[str, object]
    guest_member: SeededMember

    async def resume_connection(self) -> websockets.ClientConnection:
        return await connect_as(self.port, self.guest_member)

    async def resume_guest_seat(self) -> tuple[websockets.ClientConnection, dict[str, object]]:
        frame = {
            "type": SignalType.RESUME.value,
            "participant_id": self.seat["participant_id"],
            "token": self.seat["resume_token"],
            "last_seq": self.seat["seq"],
            "sent_floor": 0,
        }
        resumed_guest = await self.resume_connection()
        return resumed_guest, await _say(resumed_guest, frame)

    async def next_frame_the_host_hears(self) -> dict[str, object]:
        return json.loads(await asyncio.wait_for(self.host.recv(), ARRIVAL_CAP_SECONDS))


async def connect_as(port: int, member: SeededMember | None) -> websockets.ClientConnection:
    return await websockets.connect(f"ws://{LOCALHOST}:{port}/ws", additional_headers=handshake_headers(member))


async def _say(socket: websockets.ClientConnection, frame: dict[str, object]) -> dict[str, object]:
    await socket.send(json.dumps(frame))
    return json.loads(await socket.recv())


@pytest_asyncio.fixture
async def call(sign_in_settings) -> AsyncIterator[Call]:
    listening = socket.socket()
    listening.bind((LOCALHOST, 0))
    listening.listen()
    port = listening.getsockname()[1]
    stores = SignInStores.in_memory()
    app = create_app(sign_in_settings().settings, stores=stores)
    web_auth = installed_web_auth_config(app)
    host_member = seed_member(stores, web_auth)
    guest_member = seed_member(stores, web_auth)
    config = uvicorn.Config(
        app,
        ws=UVICORN_WEBSOCKET_IMPLEMENTATION,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve(sockets=[listening]))
    host = await connect_as(port, host_member)
    room = await _say(host, {"type": SignalType.JOIN.value})
    guest = await connect_as(port, guest_member)
    seat = await _say(guest, {"type": SignalType.JOIN.value, "room_id": room["room_id"]})
    await host.recv()
    yield Call(port, host, guest, seat, guest_member)
    server.should_exit = True
    await serving


@pytest.mark.integration
class TestRealTransport:
    @pytest.mark.asyncio
    async def test_aborted_connection_parks_the_seat_and_the_token_resumes_it(self, call: Call) -> None:
        call.guest.transport.abort()

        resumed_guest, resumed = await call.resume_guest_seat()
        barrier = {"type": SignalType.CHAT.value, "message": BARRIER_MESSAGE, SEQUENCE_KEY: FIRST_CLIENT_SEQUENCE}
        await resumed_guest.send(json.dumps(barrier))

        assert resumed["type"] == SignalType.RESUMED.value
        assert (await call.next_frame_the_host_hears())["type"] == SignalType.CHAT.value

    @pytest.mark.asyncio
    async def test_client_closing_with_a_clean_code_leaves_without_parking(self, call: Call) -> None:
        await call.guest.close(CLEAN_CLOSE_CODE)

        assert (await call.next_frame_the_host_hears())["type"] == SignalType.PARTICIPANT_LEFT.value

    @pytest.mark.asyncio
    async def test_handshake_without_a_session_is_answered_with_forbidden(self, call: Call) -> None:
        with pytest.raises(websockets.exceptions.InvalidStatus) as refused:
            await connect_as(call.port, None)

        assert refused.value.response.status_code == FORBIDDEN_STATUS
