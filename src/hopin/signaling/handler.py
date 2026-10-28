from functools import partial
from typing import TYPE_CHECKING, assert_never

import structlog
from starlette.websockets import WebSocket  # noqa: TC002 - FastAPI resolves the route annotation at runtime

from hopin.config.constants import (
    NORMAL_CLOSE_CODES,
    POLICY_VIOLATION_CLOSE_CODE,
    SERVER_ERROR_CLOSE_CODE,
    SESSION_ENDED_CLOSE_CODE,
    SESSION_RECHECK_SECONDS,
    WEBSOCKET_DISCONNECT_EVENT,
)
from hopin.errors import HopInError, InvalidMessageError, TooManyCallsError
from hopin.signaling.frames import (
    ChatFrame,
    DispatchedFrame,
    EndRoomFrame,
    JoinFrame,
    LockFrame,
    MediaStateFrame,
    PeerId,
    RelayFrame,
    ResumeFrame,
    UnlockFrame,
    decode_frame,
    error_frame,
)
from hopin.signaling.routing import RoomRouting
from hopin.signaling.seats import Connection, Peer, SeatSnapshot

if TYPE_CHECKING:
    from hopin.rooms.manager import RoomManager
    from hopin.signaling.identity import IdentityResolver
    from hopin.signaling.scheduler import Scheduler, TimerHandle

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class SignalingHandler:
    def __init__(self, room_manager: RoomManager, identity_resolver: IdentityResolver, scheduler: Scheduler) -> None:
        self._room_manager = room_manager
        self._identity_resolver = identity_resolver
        self._scheduler = scheduler
        self._routing = RoomRouting(room_manager, scheduler)
        self._seats = self._routing.seats

    def seats_of(self, account_id: str) -> tuple[SeatSnapshot, ...]:
        """The seats an account holds, parked ones included; synchronous so no coroutine changes a seat meanwhile."""
        return tuple(self._snapshot_of(peer_id, peer) for peer_id, peer in self._seats.held_by(account_id))

    def _snapshot_of(self, peer_id: PeerId, peer: Peer) -> SeatSnapshot:
        room_id = self._routing.room_of(peer_id)
        hosted = room_id is not None and self._room_manager.get_room(room_id).is_hosted_by(peer.account)
        return SeatSnapshot(call_id=room_id, host=hosted, replay_frames=len(peer.log))

    async def handle_websocket(self, websocket: WebSocket) -> None:
        account = await self._identity_resolver.resolve(websocket)
        if account is None:
            await websocket.close(code=POLICY_VIOLATION_CLOSE_CODE)
            return

        await websocket.accept()
        try:
            peer_id = self._seats.open_seat(account, websocket)
        except TooManyCallsError as refusal:
            await self._refuse_beyond_cap(websocket, refusal)
            return
        await logger.ainfo("client_connected", peer_id=peer_id)

        connection = Connection(websocket=websocket, peer_id=peer_id)
        connection.recheck = self._schedule_recheck(connection)
        try:
            await self._process_messages(connection)
        finally:
            await self._end_connection(connection)

    async def _refuse_beyond_cap(self, websocket: WebSocket, refusal: TooManyCallsError) -> None:
        await logger.ainfo("too_many_calls", **refusal.frame_fields())
        await self._seats.write(websocket, error_frame(refusal))
        await self._seats.close_socket(websocket, POLICY_VIOLATION_CLOSE_CODE)

    async def _process_messages(self, connection: Connection) -> None:
        while True:
            frame = await connection.websocket.receive()
            if frame["type"] == WEBSOCKET_DISCONNECT_EVENT:
                if frame["code"] not in NORMAL_CLOSE_CODES and not self._seats.closed_by_server(connection.websocket):
                    connection.dropped = True
                    await logger.aerror("websocket_error", peer_id=connection.peer_id, code=frame["code"])
                return
            text = frame.get("text")
            if text is not None:
                if not await self._session_admits(connection):
                    return
                if self._serves_seat(connection):
                    await self._handle_message(connection, text)

    def _schedule_recheck(self, connection: Connection) -> TimerHandle:
        return self._scheduler.call_later(SESSION_RECHECK_SECONDS, partial(self._recheck_on_tick, connection))

    async def _recheck_on_tick(self, connection: Connection) -> None:
        if connection.ended:
            return
        if await self._session_admits(connection) and self._serves_seat(connection):
            connection.recheck = self._schedule_recheck(connection)

    async def _session_admits(self, connection: Connection) -> bool:
        try:
            account = await self._identity_resolver.resolve(connection.websocket)
        except Exception:
            await self._end_session(connection, SERVER_ERROR_CLOSE_CODE)
            raise
        if account is not None:
            return True
        await self._end_session(connection, SESSION_ENDED_CLOSE_CODE)
        return False

    def _holds_seat(self, connection: Connection) -> bool:
        peer = self._seats.find(connection.peer_id)
        return peer is not None and peer.websocket is connection.websocket

    def _serves_seat(self, connection: Connection) -> bool:
        return not connection.ended and self._holds_seat(connection)

    async def _end_session(self, connection: Connection, close_code: int) -> None:
        # A check can be suspended across a drop or a takeover; the seat then belongs to someone else.
        if not self._serves_seat(connection):
            return
        peer_id = connection.peer_id
        connection.ended = True
        # The seat leaves before anything awaits, so a drop or a resume read meanwhile finds no seat; the room
        # hears of it only after the close, so another participant's slow socket cannot hold this one open.
        departure = self._routing.leave(peer_id)
        await self._seats.close_socket(connection.websocket, close_code)
        await self._routing.announce(departure)
        await logger.ainfo("session_ended", peer_id=peer_id)

    async def _end_connection(self, connection: Connection) -> None:
        connection.ended = True
        connection.recheck.cancel()
        if not self._holds_seat(connection):
            return
        if connection.dropped and self._routing.room_of(connection.peer_id) is not None:
            await self._seats.park(connection.peer_id, self._seats.seat(connection.peer_id))
        else:
            await self._routing.handle_disconnect(connection.peer_id)

    async def _handle_message(self, connection: Connection, raw_data: str) -> None:
        peer_id = connection.peer_id
        peer = self._seats.seat(peer_id)
        is_first_frame = connection.frames_received == 0
        connection.frames_received += 1

        try:
            incoming = decode_frame(raw_data)
        except InvalidMessageError as refusal:
            await self._send_error(peer_id, refusal)
            return

        if isinstance(incoming.frame, ResumeFrame):
            await self._seats.resume(connection, incoming.frame, is_first_frame=is_first_frame)
            return

        if not is_first_frame:
            if incoming.sequence is None:
                await self._send_error(peer_id, InvalidMessageError("Missing sequence number"))
                return
            if incoming.sequence <= peer.received_seq:
                return
            peer.received_seq = incoming.sequence

        try:
            if isinstance(incoming.frame, InvalidMessageError):
                raise incoming.frame
            await self._dispatch(peer_id, incoming.frame)
        except HopInError as exc:
            await self._send_error(peer_id, exc)

    async def _dispatch(self, peer_id: PeerId, frame: DispatchedFrame) -> None:
        match frame:
            case JoinFrame():
                await self._routing.handle_join(peer_id, frame)
            case RelayFrame():
                await self._routing.handle_relay(peer_id, frame)
            case LockFrame():
                await self._routing.set_locked(peer_id, locked=True)
            case UnlockFrame():
                await self._routing.set_locked(peer_id, locked=False)
            case ChatFrame():
                await self._routing.handle_chat(peer_id, frame)
            case MediaStateFrame():
                await self._routing.handle_media_state(peer_id, frame)
            case EndRoomFrame():
                await self._routing.handle_end_room(peer_id)
            case _:  # pragma: no cover - the type checker proves the match exhaustive
                assert_never(frame)

    async def _send_error(self, peer_id: PeerId, error: HopInError) -> None:
        await self._seats.send(peer_id, error_frame(error))
