import json
import secrets
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING

import structlog
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from hopin.config.constants import PEER_ID_BYTES, RESUME_TOKEN_BYTES, RESUME_WINDOW_SECONDS
from hopin.signaling.frames import JsonObject, PeerId
from hopin.signaling.replay import ReplayLog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from hopin.rooms.models import Account
    from hopin.signaling.scheduler import Scheduler, TimerHandle

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


@dataclass(frozen=True)
class Park:
    generation: int
    dropped_at: float
    timer: TimerHandle


@dataclass(frozen=True)
class SeatSnapshot:
    call_id: str | None
    host: bool
    replay_frames: int


def mint_peer_id() -> PeerId:
    return PeerId(secrets.token_urlsafe(PEER_ID_BYTES))


def mint_resume_token() -> str:
    return secrets.token_urlsafe(RESUME_TOKEN_BYTES)


@dataclass
class Peer:
    """A seat: the account behind it, the socket that currently serves it and what a resume needs."""

    account: Account
    websocket: WebSocket
    resume_token: str = field(default_factory=mint_resume_token, repr=False)
    log: ReplayLog = field(default_factory=ReplayLog, repr=False)
    received_seq: int = 0
    replaying: bool = False
    park: Park | None = None
    parks_started: int = 0


@dataclass
class Connection:
    """One socket's run through the message loop; its peer id changes when it resumes a seat."""

    websocket: WebSocket
    peer_id: PeerId
    frames_received: int = 0
    dropped: bool = False
    recheck: TimerHandle = field(init=False)
    ended: bool = False


class Seats:
    """The seat registry: who holds which seat, what was sent to it and when a parked one lapses."""

    def __init__(self, scheduler: Scheduler, on_seat_lost: Callable[[PeerId], Awaitable[None]]) -> None:
        self._scheduler = scheduler
        self._on_seat_lost = on_seat_lost
        self._peers: dict[PeerId, Peer] = {}

    def open_seat(self, account: Account, websocket: WebSocket) -> PeerId:
        peer_id = mint_peer_id()
        self._peers[peer_id] = Peer(account=account, websocket=websocket)
        return peer_id

    def find(self, peer_id: PeerId) -> Peer | None:
        return self._peers.get(peer_id)

    def seat(self, peer_id: PeerId) -> Peer:
        return self._peers[peer_id]

    def held_by(self, account_id: str) -> tuple[tuple[PeerId, Peer], ...]:
        return tuple((peer_id, peer) for peer_id, peer in self._peers.items() if peer.account.id == account_id)

    def forget(self, peer_id: PeerId) -> Peer | None:
        peer = self._peers.pop(peer_id, None)
        if peer is not None and peer.park is not None:
            peer.park.timer.cancel()
        return peer

    async def park(self, peer_id: PeerId, peer: Peer) -> None:
        peer.parks_started += 1
        generation = peer.parks_started
        timer = self._scheduler.call_later(RESUME_WINDOW_SECONDS, partial(self._expire_park, peer_id, generation))
        peer.park = Park(generation=generation, dropped_at=self._scheduler.now(), timer=timer)
        peer.replaying = False
        await logger.ainfo("seat_parked", peer_id=peer_id)

    async def _expire_park(self, peer_id: PeerId, generation: int) -> None:
        peer = self._peers.get(peer_id)
        if peer is not None and peer.park is not None and peer.park.generation == generation:
            await self._on_seat_lost(peer_id)

    def account_of(self, peer_id: str) -> Account:
        return self._peers[PeerId(peer_id)].account

    async def send_all(self, peer_ids: list[str], data: JsonObject) -> None:
        for peer_id in peer_ids:
            await self.send(peer_id, data)

    async def send(self, peer_id: str, data: JsonObject) -> None:
        # The browser drops frames at or below the last seq it dispatched, so nothing may await
        # between assigning a frame's seq in the log and writing it.
        peer = self._peers.get(PeerId(peer_id))
        if peer is None:
            return
        frame = peer.log.append(data)
        if peer.replaying or peer.park is not None:
            return
        await self.write(peer.websocket, frame, peer_id)

    @staticmethod
    async def write(websocket: WebSocket, frame: JsonObject, peer_id: str | None = None) -> bool:
        if not is_connected(websocket):
            return False
        try:
            await websocket.send_text(json.dumps(frame))
        except WebSocketDisconnect:
            await logger.ainfo("send_to_gone_peer", peer_id=peer_id)
            return False
        return True

    def credentials(self, peer_id: PeerId) -> dict[str, str]:
        return {"participant_id": peer_id, "resume_token": self._peers[peer_id].resume_token}


def closed_by_server(websocket: WebSocket) -> bool:
    return websocket.application_state == WebSocketState.DISCONNECTED


def is_connected(websocket: WebSocket) -> bool:
    return (
        websocket.client_state == WebSocketState.CONNECTED and websocket.application_state == WebSocketState.CONNECTED
    )
