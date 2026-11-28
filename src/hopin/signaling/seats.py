import json
import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from weakref import WeakSet

import structlog
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from hopin.config.constants import PEER_ID_BYTES, RESUME_TOKEN_BYTES, SEAT_CLOSED_BY_SERVER_CODE
from hopin.signaling.frames import RESUME_TOKEN_KEY, TOKEN_BEARING_TYPES, TYPE_KEY, JsonObject, PeerId

if TYPE_CHECKING:
    from starlette.requests import HTTPConnection

    from hopin.signaling.call_store import CallStore
    from hopin.signaling.scheduler import TimerHandle

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


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
class SeatSocket:
    """The part of a seat that belongs to the socket serving it now; a resume replaces it with the new socket's. Its
    `unsent_token` is the seat's token as long as no frame that carries it has been written."""

    websocket: WebSocket
    unsent_token: str | None = field(default=None, repr=False)
    delivered_seq: int = 0
    replaying: bool = False
    writers_started: int = 0
    outcome_times: tuple[float, ...] = ()
    generation: str = ""


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
    """The sockets this process holds for its seats, and what is written to them: the logged frames of a seat's log in
    the call store, which only the store's commit writes."""

    def __init__(self, store: CallStore) -> None:
        self._store = store
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

    def peers(self) -> frozenset[PeerId]:
        """The seats whose sockets this process holds."""
        return frozenset(self._sockets)

    def sockets_outside(self, generation: str) -> list[SeatSocket]:
        """The sockets of seats opened under another generation than this one."""
        return [socket for socket in self._sockets.values() if socket.generation != generation]

    def connection_of(self, peer_id: PeerId) -> HTTPConnection:
        return self._sockets[peer_id].websocket

    async def deliver(self, peer_id: PeerId) -> None:
        """Write the logged frames the seat's socket has not had; held while the seat is parked or replaying."""
        # The browser drops frames at or below the last seq it dispatched, so each write starts in the order of the
        # seq, claimed with no await before it starts; a slow write never holds the next one back, and a writer whose
        # slow write ends leaves the frames after it to the newer writer, so none of them overtakes the newer one's.
        socket = self._sockets.get(peer_id)
        if socket is None:
            return
        socket.writers_started += 1
        writer = socket.writers_started
        while self._is_writer(peer_id, socket, writer):
            read = await self._store.read_log(peer_id, socket.delivered_seq)
            if not self._is_writer(peer_id, socket, writer) or read is None or read.parked or not read.frames:
                return
            pending = read.frames[0]
            socket.delivered_seq = pending.seq
            await self.write(socket.websocket, _with_unsent_token(socket, pending.frame), peer_id)

    def _is_writer(self, peer_id: PeerId, socket: SeatSocket, writer: int) -> bool:
        return self._sockets.get(peer_id) is socket and not socket.replaying and socket.writers_started == writer

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
            read = await self._store.read_log(peer_id, cursor)
            if read is None or cursor < read.floor:
                return False
            if not read.frames:
                socket.delivered_seq = cursor
                socket.replaying = False
                # A frame logged while the log was read found the socket replaying and was held.
                await self.deliver(peer_id)
                return True
            if not await self.write(socket.websocket, read.frames[0].frame):
                return True
            cursor = read.frames[0].seq
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
