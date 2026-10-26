import asyncio
import json
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest
from starlette.websockets import WebSocketState

from hopin.config.constants import WEBSOCKET_DISCONNECT_EVENT
from hopin.rooms.manager import RoomManager
from hopin.rooms.models import Account
from hopin.signaling.handler import SignalingHandler

ROOM_LIMIT = 5


@pytest.fixture
def room_manager():
    return RoomManager(max_participants_per_room=ROOM_LIMIT)


class SocketAccountResolver:
    async def resolve(self, websocket):
        return websocket.account


ADMITTED_ACCOUNT = Account(id="c1", name="c1")


class ManualScheduler:
    """Runs timers only when the test advances its clock; cancel leaves a started callback alone."""

    def __init__(self):
        self._now = 0.0
        self._timers = []

    def now(self):
        return self._now

    def call_later(self, delay_seconds, callback):
        timer = ManualTimer(self._now + delay_seconds, callback)
        self._timers.append(timer)
        return timer

    def pending_timers(self):
        return [timer for timer in self._timers if timer.pending]

    def has_pending_timer(self):
        return bool(self.pending_timers())

    async def advance(self, seconds):
        target = self._now + seconds
        while due := [timer for timer in self._timers if timer.pending and timer.deadline <= target]:
            timer = min(due, key=lambda candidate: candidate.deadline)
            self._now = max(self._now, timer.deadline)
            timer.pending = False
            await timer.callback()
        self._now = target


class ManualTimer:
    def __init__(self, deadline, callback):
        self.deadline = deadline
        self.callback = callback
        self.pending = True

    def cancel(self):
        self.pending = False


@pytest.fixture
def scheduler():
    return ManualScheduler()


@pytest.fixture
def handler(room_manager, scheduler):
    return SignalingHandler(room_manager, SocketAccountResolver(), scheduler)


def make_ws_mock():
    ws = AsyncMock()
    ws.client_state = WebSocketState.CONNECTED
    ws.application_state = WebSocketState.CONNECTED
    ws.send_text = AsyncMock()
    return ws


def account_named(name):
    return Account(id=name, name=name)


def text_frame(data, seq=None):
    numbered = data if seq is None else {**data, "seq": seq}
    return {"type": "websocket.receive", "text": json.dumps(numbered)}


def disconnect_frame(code):
    return {"type": WEBSOCKET_DISCONNECT_EVENT, "code": code}


@dataclass(eq=False)
class FakeSocket:
    """A minimal socket: the test pushes inbound frames and reads what the handler sent."""

    frames: list[dict]
    account: Account | None = ADMITTED_ACCOUNT
    client_state: WebSocketState = WebSocketState.CONNECTED
    application_state: WebSocketState = WebSocketState.CONNECTED
    accepted: bool = False
    closed_with: int | None = None
    sent: list[dict] = field(default_factory=list)
    send_gate: asyncio.Event | None = None
    idle: asyncio.Event = field(default_factory=asyncio.Event)
    inbox: asyncio.Queue = field(default_factory=asyncio.Queue)

    def __post_init__(self) -> None:
        for frame in self.frames:
            self.inbox.put_nowait(frame)

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int) -> None:
        self.closed_with = code
        self.application_state = WebSocketState.DISCONNECTED

    async def receive(self) -> dict:
        if self.inbox.empty():
            self.idle.set()
        frame = await self.inbox.get()
        self.idle.clear()
        if frame["type"] == WEBSOCKET_DISCONNECT_EVENT:
            self.client_state = WebSocketState.DISCONNECTED
        return frame

    def push(self, frame: dict) -> None:
        self.idle.clear()
        self.inbox.put_nowait(frame)

    async def send_text(self, text: str) -> None:
        if self.send_gate is not None:
            await self.send_gate.wait()
        self.sent.append(json.loads(text))
