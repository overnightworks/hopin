import hashlib
import json
import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from weakref import WeakSet

import structlog
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from hopin.config.constants import PEER_ID_BYTES, RESUME_TOKEN_BYTES, SEAT_CLOSED_BY_SERVER_CODE
from hopin.signaling.frames import RESUME_TOKEN_KEY, TOKEN_BEARING_TYPES, TYPE_KEY, JsonObject, PeerId
from hopin.signaling.replay import ReplayLog

if TYPE_CHECKING:
    from collections.abc import Mapping

    from starlette.requests import HTTPConnection

    from hopin.rooms.models import Account
    from hopin.signaling.scheduler import TimerHandle

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


@dataclass(frozen=True)
class Park:
    dropped_at: float


@dataclass(frozen=True)
class SeatSnapshot:
    call_id: str | None
    host: bool
    replay_frames: int


def mint_peer_id() -> PeerId:
    return PeerId(secrets.token_urlsafe(PEER_ID_BYTES))


def mint_resume_token() -> str:
    return secrets.token_urlsafe(RESUME_TOKEN_BYTES)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


@dataclass
class SeatRecord:
    """The stored part of a seat: the account behind it, its room, its park, what the browser has sent, the hash of its
    resume token and the log of what it was sent. The call store's commit is its only writer."""

    account: Account
    token_hash: bytes = field(repr=False)
    room_id: str | None = None
    park: Park | None = None
    received_seq: int = 0
    log: ReplayLog = field(default_factory=ReplayLog, repr=False)

    def accepts(self, token: str) -> bool:
        """Whether the token is the seat's, compared by its hash in constant time."""
        try:
            claimed = hash_token(token)
        except UnicodeEncodeError:
            return False
        return secrets.compare_digest(claimed, self.token_hash)


@dataclass
class SeatSocket:
    """The part of a seat that belongs to the socket serving it now; a resume replaces it with the new socket's. Its
    `unsent_token` is the seat's token as long as no frame that carries it has been written."""

    websocket: WebSocket
    unsent_token: str | None = field(default=None, repr=False)
    delivered_seq: int = 0
    replaying: bool = False
    writers_started: int = 0
    outcome_times: tuple[float, ...] = ()


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
    """The sockets this process holds for its seats, and what is written to them: the logged frames of a seat's stored
    record, which only the call store's commit writes."""

    def __init__(self, records: Mapping[PeerId, SeatRecord]) -> None:
        self._records = records
        self._sockets: dict[PeerId, SeatSocket] = {}
        self._closed_by_server: WeakSet[WebSocket] = WeakSet()

    def attach(self, peer_id: PeerId, socket: SeatSocket) -> None:
        """Make the socket the one that serves the seat; the call store's commit does it with the seat's record."""
        self._sockets[peer_id] = socket

    def detach(self, peer_id: PeerId) -> None:
        self._sockets.pop(peer_id, None)

    def find(self, peer_id: PeerId) -> SeatSocket | None:
        return self._sockets.get(peer_id)

    def socket_of(self, peer_id: PeerId) -> SeatSocket:
        return self._sockets[peer_id]

    def connection_of(self, peer_id: PeerId) -> HTTPConnection:
        return self._sockets[peer_id].websocket

    async def deliver(self, peer_id: PeerId) -> None:
        """Write the logged frames the seat's socket has not had; held while the seat is parked or replaying."""
        # The browser drops frames at or below the last seq it dispatched, so each write starts in the order of the
        # seq, claimed with no await before it starts; a slow write never holds the next one back, and a writer whose
        # slow write ends leaves the frames after it to the newer writer, so none of them overtakes the newer one's.
        socket = self._sockets.get(peer_id)
        record = self._records.get(peer_id)
        if socket is None or record is None:
            return
        socket.writers_started += 1
        writer = socket.writers_started
        while (
            self._sockets.get(peer_id) is socket
            and not socket.replaying
            and record.park is None
            and socket.writers_started == writer
        ):
            pending = record.log.since(socket.delivered_seq)
            if not pending:
                return
            socket.delivered_seq = pending[0].seq
            await self.write(socket.websocket, _with_unsent_token(socket, pending[0].frame), peer_id)

    @staticmethod
    async def write(websocket: WebSocket, frame: JsonObject, peer_id: str | None = None) -> bool:
        if not is_connected(websocket):
            return False
        try:
            await websocket.send_text(json.dumps(frame))
        except WebSocketDisconnect:
            await logger.ainfo("frame_send.refused", reason="peer_gone", peer_id=peer_id)
            return False
        return True

    async def replay(self, peer_id: PeerId, socket: SeatSocket, last_seq: int) -> bool:
        """Write the seat's logged frames after the seq the browser last dispatched, then open the socket to the frames
        that follow; false when the log no longer reaches back to that seq."""
        cursor = last_seq
        while self._sockets.get(peer_id) is socket:
            record = self._records[peer_id]
            if not record.log.reaches_back_to(cursor):
                return False
            pending = record.log.since(cursor)
            if not pending:
                socket.delivered_seq = cursor
                socket.replaying = False
                return True
            if not await self.write(socket.websocket, pending[0].frame):
                return True
            cursor = pending[0].seq
        return True

    def closed_by_server(self, websocket: WebSocket) -> bool:
        """Whether the server closed this socket itself, so an abnormal close read from it is no drop to log.

        No park hinges on it: every server close first removes the seat, hands it on or ends its room.
        """
        return websocket in self._closed_by_server

    async def close_socket(self, websocket: WebSocket, code: int = SEAT_CLOSED_BY_SERVER_CODE) -> None:
        # Recorded before the transport guard: a failed write may already have marked this socket gone,
        # and the server's close must still keep a lost echo from being logged as a drop.
        self._closed_by_server.add(websocket)
        if not is_connected(websocket):
            return
        try:
            await websocket.close(code=code)
        except WebSocketDisconnect:
            await logger.ainfo("socket_close.refused", reason="socket_gone")


def _with_unsent_token(socket: SeatSocket, frame: JsonObject) -> JsonObject:
    """The frame as this socket writes it: the first token-bearing one carries the seat's token, which no log keeps."""
    if socket.unsent_token is None or frame[TYPE_KEY] not in TOKEN_BEARING_TYPES:
        return frame
    token, socket.unsent_token = socket.unsent_token, None
    return {**frame, RESUME_TOKEN_KEY: token}


def is_connected(websocket: WebSocket) -> bool:
    return (
        websocket.client_state == WebSocketState.CONNECTED and websocket.application_state == WebSocketState.CONNECTED
    )
