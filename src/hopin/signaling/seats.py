import json
import secrets
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING
from weakref import WeakSet

import structlog
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from hopin.config.constants import (
    MAX_LIVE_SEATS_PER_ACCOUNT,
    PEER_ID_BYTES,
    RESUME_TOKEN_BYTES,
    RESUME_WINDOW_SECONDS,
    SEAT_CLOSED_BY_SERVER_CODE,
    ResumeRefusalCause,
    SignalType,
)
from hopin.errors import ResumeRefusedError, TooManyCallsError
from hopin.signaling.frames import JsonObject, PeerId, ResumeClaim, ResumeFrame, error_frame
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
        self._closed_by_server: WeakSet[WebSocket] = WeakSet()

    def open_seat(self, account: Account, websocket: WebSocket) -> PeerId:
        """Opens a seat; a parked seat holds no socket, so only live ones count toward the account's cap."""
        live_seats = sum(1 for _, peer in self.held_by(account.id) if peer.park is None)
        if live_seats >= MAX_LIVE_SEATS_PER_ACCOUNT:
            raise TooManyCallsError
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

    async def resume(self, connection: Connection, frame: ResumeFrame, *, is_first_frame: bool) -> None:
        try:
            if not is_first_frame:
                raise ResumeRefusedError(ResumeRefusalCause.NOT_FIRST_FRAME)
            claim = frame.require_claim()
            seat = self._matching_seat(connection, claim)
            gap = self._gap_before_resume(seat, claim)
            if gap is not None:
                await self._release_unresumable_seat(claim.participant_id)
                raise ResumeRefusedError(gap)
        except ResumeRefusedError as refusal:
            await self._refuse_resume(connection, refusal)
            return
        await self._take_over_seat(connection, seat, claim)

    def _matching_seat(self, connection: Connection, claim: ResumeClaim) -> Peer:
        seat = self.find(claim.participant_id)
        if seat is None:
            raise ResumeRefusedError(ResumeRefusalCause.UNKNOWN_SEAT)
        if not _token_matches(claim.token, seat.resume_token):
            raise ResumeRefusedError(ResumeRefusalCause.CREDENTIAL_MISMATCH)
        if self.account_of(connection.peer_id).id != seat.account.id:
            raise ResumeRefusedError(ResumeRefusalCause.WRONG_ACCOUNT)
        return seat

    @staticmethod
    def _gap_before_resume(seat: Peer, claim: ResumeClaim) -> ResumeRefusalCause | None:
        if not seat.log.reaches_back_to(claim.last_seq):
            return ResumeRefusalCause.SERVER_LOG_TOO_SHORT
        if claim.sent_floor > seat.received_seq:
            return ResumeRefusalCause.BROWSER_LOG_TOO_SHORT
        return None

    async def _release_unresumable_seat(self, peer_id: PeerId) -> None:
        seat = self.seat(peer_id)
        await self._on_seat_lost(peer_id)
        await self.close_socket(seat.websocket)

    def closed_by_server(self, websocket: WebSocket) -> bool:
        """Whether the server closed this socket itself, as opposed to the network dropping it."""
        return websocket in self._closed_by_server

    async def close_socket(self, websocket: WebSocket, code: int = SEAT_CLOSED_BY_SERVER_CODE) -> None:
        # The server's decision is the fact, whatever the transport state: a failed write may already
        # have marked this socket gone, and its drop must still not park the seat.
        self._closed_by_server.add(websocket)
        if not is_connected(websocket):
            return
        try:
            await websocket.close(code=code)
        except WebSocketDisconnect:
            await logger.ainfo("closed_socket_already_gone")

    async def _refuse_resume(self, connection: Connection, refusal: ResumeRefusedError) -> None:
        await logger.ainfo("resume_refused", peer_id=connection.peer_id, cause=refusal.cause.value)
        await self.write(connection.websocket, error_frame(refusal))

    async def _take_over_seat(self, connection: Connection, seat: Peer, claim: ResumeClaim) -> None:
        superseded = seat.websocket
        if seat.park is not None:
            seat.park.timer.cancel()
            seat.park = None
        self.forget(connection.peer_id)
        connection.peer_id = claim.participant_id
        seat.websocket = connection.websocket
        seat.resume_token = mint_resume_token()
        seat.replaying = True

        await self.close_socket(superseded)
        await self.write(
            connection.websocket,
            {
                "type": SignalType.RESUMED.value,
                "resume_token": seat.resume_token,
                "last_seq": seat.received_seq,
            },
        )
        await self._replay(connection.websocket, claim.participant_id, seat, claim.last_seq)
        await logger.ainfo("seat_resumed", peer_id=claim.participant_id)

    async def _replay(self, websocket: WebSocket, peer_id: PeerId, seat: Peer, last_seq: int) -> None:
        cursor = last_seq
        while self.find(peer_id) is seat and seat.websocket is websocket:
            if not seat.log.reaches_back_to(cursor):
                await self._release_unresumable_seat(peer_id)
                return
            pending = seat.log.since(cursor)
            if not pending:
                seat.replaying = False
                return
            if not await self.write(websocket, pending[0].frame):
                return
            cursor = pending[0].seq


def _token_matches(claimed: str, expected: str) -> bool:
    try:
        claimed_bytes = claimed.encode()
    except UnicodeEncodeError:
        return False
    return secrets.compare_digest(claimed_bytes, expected.encode())


def is_connected(websocket: WebSocket) -> bool:
    return (
        websocket.client_state == WebSocketState.CONNECTED and websocket.application_state == WebSocketState.CONNECTED
    )
