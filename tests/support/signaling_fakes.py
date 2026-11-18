import asyncio
import json
import threading
from dataclasses import dataclass, field
from types import MappingProxyType, SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from fastapi import APIRouter
from starlette.websockets import WebSocketState

from hopin.config.constants import WEBSOCKET_DISCONNECT_EVENT
from hopin.errors import InvalidMessageError
from hopin.rooms.models import Account
from hopin.signaling.features import TransitionFeature
from hopin.signaling.transition import Section, Work

if TYPE_CHECKING:
    from collections.abc import Mapping

ROOM_LIMIT = 5
ARRIVAL_CAP_SECONDS = 5


class SocketAccountResolver:
    async def resolve(self, websocket):
        return websocket.account


ADMITTED_ACCOUNT = Account(id="c1", name="c1")


class ManualScheduler:
    """Runs timers only when the test advances its clock; cancel leaves a started callback alone."""

    def __init__(self):
        self._now = 0.0
        self._timers = []
        self._timer_set = threading.Condition()

    def now(self):
        return self._now

    def call_later(self, delay_seconds, callback):
        timer = ManualTimer(self._now + delay_seconds, callback, delay_seconds)
        with self._timer_set:
            self._timers.append(timer)
            self._timer_set.notify_all()
        return timer

    def wait_for_timer(self, delay_seconds):
        """Waits, on the test's thread, until a timer of this delay is pending; the server sets its timers on its own
        loop, so a test that moves the clock after an effect it caused waits for that timer here first."""
        with self._timer_set:
            return self._timer_set.wait_for(
                lambda: any(timer.pending and timer.delay == delay_seconds for timer in self._timers),
                timeout=ARRIVAL_CAP_SECONDS,
            )

    def pending_timers(self):
        return [timer for timer in self._timers if timer.pending]

    def has_pending_timer(self):
        return bool(self.pending_timers())

    def skip(self, seconds):
        """Move the clock without running the timers that fall due."""
        self._now += seconds

    async def advance(self, seconds):
        target = self._now + seconds
        while due := [timer for timer in self._timers if timer.pending and timer.deadline <= target]:
            timer = min(due, key=lambda candidate: candidate.deadline)
            self._now = max(self._now, timer.deadline)
            timer.pending = False
            await timer.callback()
        self._now = target

    async def run_cancelled(self):
        """Run the callbacks cancelled after they had started, as a real loop does when a timer fires before its
        cancel."""
        for timer in [timer for timer in self._timers if timer.cancelled]:
            await timer.callback()


class ManualTimer:
    def __init__(self, deadline, callback, delay):
        self.deadline = deadline
        self.callback = callback
        self.delay = delay
        self.pending = True
        self.cancelled = False

    def cancel(self):
        self.pending = False
        self.cancelled = True


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
    arrived: asyncio.Event = field(default_factory=asyncio.Event)

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
        self.arrived.set()

    async def wait_until(self, condition) -> None:
        """Wait until the condition holds, checking it again as each frame arrives rather than by time."""
        async with asyncio.timeout(ARRIVAL_CAP_SECONDS):
            while not condition():
                self.arrived.clear()
                await self.arrived.wait()


@dataclass(eq=False)
class RecordingFeature(TransitionFeature):
    """A call feature that records what the call core hands it and handles only the frame types it is given."""

    handles: frozenset[str] = frozenset()
    name: str = "recording"
    browser_modules: tuple[str, ...] = ()
    signal_types: Mapping[str, str] = MappingProxyType({})
    offered: list = field(default_factory=list)
    joined: list = field(default_factory=list)
    left: list = field(default_factory=list)
    changed: list = field(default_factory=list)
    said: list = field(default_factory=list)

    def on_frame(self, view, peer_id, frame, transition):
        self.offered.append((peer_id, frame))
        return frame.signal_type in self.handles

    def seat_joined(self, view, peer_id, transition):
        self.joined.append(peer_id)

    def seat_left(self, view, peer_id, transition):
        self.left.append((peer_id, view.room_id, view.name_of(peer_id)))

    def seat_changed(self, view, peer_id, change):
        """Records the seat's standing as it is the moment the call core announces the change."""
        in_room = view.room_of(peer_id) is not None
        self.changed.append((peer_id, in_room, in_room and view.is_parked(peer_id)))

    def chat_said(self, view, peer_id, message, change):
        self.said.append((peer_id, view.room_id, message))


STAGED_LINE = Section[str]("staged_line")


class StagingFeature(TransitionFeature):
    """A call feature on transitions: `stage` keeps its line as the room's state and tells every seat the line; a line
    `refused` is kept and told, then refused. `show` tells its sender the line the room keeps."""

    name = "staging"
    browser_modules = ()
    signal_types = MappingProxyType({"STAGE": "stage", "SHOW": "show"})

    def on_frame(self, view, peer_id, frame, transition):
        if frame.signal_type == "show":
            transition.send(peer_id, {"type": "staged", "line": view.section(STAGED_LINE)})
            return True
        line = frame.data["line"]
        transition.put(STAGED_LINE, line)
        for member in view.members:
            transition.send(member, {"type": "staged", "line": line})
        if line == "refused":
            raise InvalidMessageError("The line is refused")
        return True

    def seat_joined(self, view, peer_id, transition):
        pass

    def seat_left(self, view, peer_id, transition):
        pass


class WorkingFeature(TransitionFeature):
    """A call feature on transitions with work: `work` dispatches a work of its key on the sender's seat, which runs
    until it is stopped, or raises at once when its key is `failing`; `stop` stops the work of its key. It records the
    keys it is asked to stop."""

    name = "working"
    browser_modules = ()
    signal_types = MappingProxyType({"WORK": "work", "STOP": "stop"})

    def __init__(self):
        self.stop_asked: list[str] = []
        self._stops: dict[str, asyncio.Event] = {}

    def on_frame(self, view, peer_id, frame, transition):
        key = frame.data["key"]
        if frame.signal_type == "work":
            transition.dispatch(Work(key, peer_id))
        else:
            transition.stop(key)
        return True

    def seat_joined(self, view, peer_id, transition):
        pass

    def seat_left(self, view, peer_id, transition):
        pass

    async def run_work(self, work, live_seats, room):
        if work.key == "failing":
            raise RuntimeError("the work broke")
        await self._stops.setdefault(work.key, asyncio.Event()).wait()

    def stop_work(self, key):
        self.stop_asked.append(key)
        self._stops.setdefault(key, asyncio.Event()).set()


@dataclass(frozen=True)
class WitnessFeatureBuilder:
    """A call feature in the shape the app builds from a feature: `witness` is the call feature, which adds no route
    and no bearer resource."""

    witness: RecordingFeature
    sign_in_scopes: tuple[str, ...] = ()
    frame_sources: tuple[str, ...] = ()

    def __call__(self, **_options):
        return SimpleNamespace(feature=self.witness, router=APIRouter(), bearer_resource=None)
