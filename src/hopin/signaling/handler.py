import math
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, assert_never

import structlog
from starlette.websockets import WebSocket  # noqa: TC002 - FastAPI resolves the route annotation at runtime

from hopin.config.constants import (
    HOST_GRACE_SECONDS,
    NORMAL_CLOSE_CODES,
    POLICY_VIOLATION_CLOSE_CODE,
    SERVER_ERROR_CLOSE_CODE,
    SESSION_ENDED_CLOSE_CODE,
    SESSION_RECHECK_SECONDS,
    WEBSOCKET_DISCONNECT_EVENT,
    RoomClosedReason,
    SignalType,
)
from hopin.errors import HopInError, InvalidMessageError, RoomNotFoundError
from hopin.signaling.frames import (
    ChatFrame,
    DispatchedFrame,
    EndRoomFrame,
    JoinFrame,
    JsonObject,
    LockFrame,
    MediaStateFrame,
    PeerId,
    RelayFrame,
    ResumeFrame,
    UnlockFrame,
    decode_frame,
)
from hopin.signaling.seats import (
    Connection,
    Peer,
    Seats,
    SeatSnapshot,
    close_socket,
    closed_by_server,
)

if TYPE_CHECKING:
    from hopin.rooms.manager import RoomManager
    from hopin.signaling.identity import IdentityResolver
    from hopin.signaling.scheduler import Scheduler, TimerHandle

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


@dataclass(frozen=True)
class HostGrace:
    host_peer_id: str
    deadline: float
    timer: TimerHandle


class SignalingHandler:
    def __init__(self, room_manager: RoomManager, identity_resolver: IdentityResolver, scheduler: Scheduler) -> None:
        self._room_manager = room_manager
        self._identity_resolver = identity_resolver
        self._scheduler = scheduler
        self._graces: dict[str, HostGrace] = {}
        self._seats = Seats(scheduler, on_seat_lost=self._handle_disconnect)
        self._peer_rooms: dict[str, str] = {}

    def seats_of(self, account_id: str) -> tuple[SeatSnapshot, ...]:
        """The seats an account holds, parked ones included; synchronous so no coroutine changes a seat meanwhile."""
        return tuple(self._snapshot_of(peer_id, peer) for peer_id, peer in self._seats.held_by(account_id))

    def _snapshot_of(self, peer_id: PeerId, peer: Peer) -> SeatSnapshot:
        room_id = self._peer_rooms.get(peer_id)
        hosted = room_id is not None and self._room_manager.get_room(room_id).is_hosted_by(peer.account)
        return SeatSnapshot(call_id=room_id, host=hosted, replay_frames=len(peer.log))

    async def handle_websocket(self, websocket: WebSocket) -> None:
        account = await self._identity_resolver.resolve(websocket)
        if account is None:
            await websocket.close(code=POLICY_VIOLATION_CLOSE_CODE)
            return

        await websocket.accept()
        peer_id = self._seats.open_seat(account, websocket)
        await logger.ainfo("client_connected", peer_id=peer_id)

        connection = Connection(websocket=websocket, peer_id=peer_id)
        connection.recheck = self._schedule_recheck(connection)
        try:
            await self._process_messages(connection)
        finally:
            await self._end_connection(connection)

    async def _process_messages(self, connection: Connection) -> None:
        while True:
            frame = await connection.websocket.receive()
            if frame["type"] == WEBSOCKET_DISCONNECT_EVENT:
                if frame["code"] not in NORMAL_CLOSE_CODES and not closed_by_server(connection.websocket):
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
        await close_socket(connection.websocket, close_code)
        await logger.ainfo("session_ended", peer_id=peer_id)
        if not self._holds_seat(connection):
            return
        await self._handle_disconnect(peer_id)

    async def _end_connection(self, connection: Connection) -> None:
        connection.ended = True
        connection.recheck.cancel()
        if not self._holds_seat(connection):
            return
        if connection.dropped and connection.peer_id in self._peer_rooms:
            await self._seats.park(connection.peer_id, self._seats.seat(connection.peer_id))
        else:
            await self._handle_disconnect(connection.peer_id)

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
                await self._handle_join(peer_id, frame)
            case RelayFrame():
                await self._handle_relay(peer_id, frame)
            case LockFrame():
                await self._set_locked(peer_id, locked=True)
            case UnlockFrame():
                await self._set_locked(peer_id, locked=False)
            case ChatFrame():
                await self._handle_chat(peer_id, frame)
            case MediaStateFrame():
                await self._handle_media_state(peer_id, frame)
            case EndRoomFrame():
                await self._handle_end_room(peer_id)
            case _:  # pragma: no cover - the type checker proves the match exhaustive
                assert_never(frame)

    async def _handle_join(self, peer_id: PeerId, frame: JoinFrame) -> None:
        room_id = frame.room_id
        if peer_id in self._peer_rooms:
            raise InvalidMessageError("This seat is already in a room")

        if room_id is None:
            await self._create_room(peer_id)
        else:
            await self._join_room(peer_id, room_id)

    async def _create_room(self, peer_id: PeerId) -> None:
        room = self._room_manager.create_room(peer_id, self._seats.account_of(peer_id))
        self._peer_rooms[peer_id] = room.room_id
        await self._seats.send(
            peer_id,
            {
                "type": SignalType.ROOM_CREATED.value,
                "room_id": room.room_id,
                "is_host": True,
                **self._seats.credentials(peer_id),
            },
        )
        await logger.ainfo("room_created", room_id=room.room_id, host=peer_id)

    async def _join_room(self, peer_id: PeerId, room_id: str) -> None:
        account = self._seats.account_of(peer_id)
        room = self._room_manager.add_participant(room_id, peer_id, account)
        self._peer_rooms[peer_id] = room_id
        host_returned = room.is_hosted_by(account) and self._cancel_grace(room_id)
        grace = self._graces.get(room_id)
        others = [pid for pid in room.participant_ids if pid != peer_id]

        await self._seats.send(
            peer_id,
            {
                "type": SignalType.EXISTING_PARTICIPANTS.value,
                # A copy: the frame goes into the seat's replay log, and a list[str] is not a JSON value list.
                "participants": [*others],
                "names": {pid: self._seats.account_of(pid).name for pid in others},
                "is_host": room.is_hosted_by(account),
                "locked": room.locked,
                **self._seats.credentials(peer_id),
            },
        )
        if grace is not None:
            await self._seats.send(peer_id, self._host_away_frame(grace))
        await self._seats.send_all(
            others,
            {"type": SignalType.PARTICIPANT_JOINED.value, "participant_id": peer_id, "name": account.name},
        )
        if host_returned:
            await self._seats.send_all(others, {"type": SignalType.HOST_BACK.value, "participant_id": peer_id})

        await logger.ainfo("participant_joined", room_id=room_id, participant=peer_id)

    async def _set_locked(self, peer_id: PeerId, *, locked: bool) -> None:
        room_id = self._peer_rooms.get(peer_id)
        if not room_id:
            return
        room = self._room_manager.set_room_locked(room_id, self._seats.account_of(peer_id), locked=locked)
        signal_type = SignalType.ROOM_LOCKED if locked else SignalType.ROOM_UNLOCKED
        await self._broadcast_to_room(room_id, {"type": signal_type.value})
        await logger.ainfo(signal_type.value, room_id=room.room_id, by=peer_id)

    async def _handle_end_room(self, peer_id: PeerId) -> None:
        room_id = self._peer_rooms.get(peer_id)
        if not room_id:
            return
        self._room_manager.get_room_for_host(room_id, self._seats.account_of(peer_id))
        await self._end_room(room_id, RoomClosedReason.ENDED)

    async def _handle_chat(self, peer_id: PeerId, frame: ChatFrame) -> None:
        room_id = self._peer_rooms.get(peer_id)
        if not room_id:
            return
        message = frame.message
        if not message:
            return
        room = self._room_manager.get_room(room_id)
        others = [pid for pid in room.participant_ids if pid != peer_id]
        await self._seats.send_all(others, {"type": SignalType.CHAT.value, "message": message, "from": peer_id})

    async def _handle_media_state(self, peer_id: PeerId, frame: MediaStateFrame) -> None:
        room_id = self._peer_rooms.get(peer_id)
        if not room_id:
            return
        room = self._room_manager.get_room(room_id)
        others = [pid for pid in room.participant_ids if pid != peer_id]
        await self._seats.send_all(
            others,
            {"type": SignalType.MEDIA_STATE.value, "from": peer_id, "audio": frame.audio, "video": frame.video},
        )

    async def _handle_relay(self, peer_id: PeerId, frame: RelayFrame) -> None:
        target_id = frame.target
        if target_id is None:
            return
        sender_room = self._peer_rooms.get(peer_id)
        if sender_room is None or self._peer_rooms.get(target_id) != sender_room:
            await logger.ainfo("relay_outside_room", peer_id=peer_id)
            return
        await self._seats.send(
            target_id,
            {
                "type": frame.kind.value,
                "sdp": frame.sdp,
                "candidate": frame.candidate,
                "from": peer_id,
            },
        )

    async def _handle_disconnect(self, peer_id: PeerId) -> None:
        peer = self._seats.forget(peer_id)
        left_at = self._scheduler.now()
        if peer is not None and peer.park is not None:
            left_at = peer.park.dropped_at
        room_id = self._peer_rooms.pop(peer_id, None)
        if not room_id:
            return

        try:
            room = self._room_manager.get_room(room_id)
        except RoomNotFoundError:
            return

        leaver = room.participants[peer_id]
        self._room_manager.remove_participant(room_id, peer_id)
        remaining = room.participant_ids

        if room.is_hosted_by(leaver.account) and not room.host_present:
            grace = self._start_grace(room_id, peer_id, left_at + HOST_GRACE_SECONDS)
            await self._seats.send_all(remaining, self._host_away_frame(grace))

        await self._seats.send_all(remaining, {"type": SignalType.PARTICIPANT_LEFT.value, "participant_id": peer_id})
        await logger.ainfo("participant_left", room_id=room_id, participant=peer_id)

    def _start_grace(self, room_id: str, host_peer_id: str, deadline: float) -> HostGrace:
        timer = self._scheduler.call_later(deadline - self._scheduler.now(), partial(self._expire_grace, room_id))
        grace = HostGrace(host_peer_id=host_peer_id, deadline=deadline, timer=timer)
        self._graces[room_id] = grace
        return grace

    def _cancel_grace(self, room_id: str) -> bool:
        grace = self._graces.pop(room_id, None)
        if grace is None:
            return False
        grace.timer.cancel()
        return True

    async def _expire_grace(self, room_id: str) -> None:
        if room_id in self._graces:
            await self._end_room(room_id, RoomClosedReason.HOST_GONE)

    async def _end_room(self, room_id: str, reason: RoomClosedReason) -> None:
        recipients = self._room_manager.get_room(room_id).participant_ids
        for peer_id in recipients:
            self._peer_rooms.pop(peer_id, None)
        self._cancel_grace(room_id)
        self._room_manager.remove_room(room_id)

        frame: JsonObject = {"type": SignalType.ROOM_CLOSED.value, "reason": reason.value}
        if reason is RoomClosedReason.HOST_GONE:
            frame["grace_seconds"] = HOST_GRACE_SECONDS
        await self._seats.send_all(recipients, frame)
        seats = [self._seats.forget(PeerId(peer_id)) for peer_id in recipients]
        for websocket in [seat.websocket for seat in seats if seat is not None]:
            await close_socket(websocket)
        await logger.ainfo("room_closed", room_id=room_id, reason=reason.value)

    def _host_away_frame(self, grace: HostGrace) -> JsonObject:
        return {
            "type": SignalType.HOST_AWAY.value,
            "seconds_left": math.ceil(grace.deadline - self._scheduler.now()),
            "participant_id": grace.host_peer_id,
        }

    async def _broadcast_to_room(self, room_id: str, data: JsonObject) -> None:
        await self._seats.send_all(self._room_manager.get_room(room_id).participant_ids, data)

    async def _send_error(self, peer_id: PeerId, error: HopInError) -> None:
        await self._seats.send(
            peer_id,
            {
                "type": SignalType.ERROR.value,
                "reason": error.reason.value,
                "message": str(error),
                **error.frame_fields(),
            },
        )
