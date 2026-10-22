import json
import math
import secrets
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, NewType, TypeGuard

import structlog
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from hopin.config.constants import (
    HOST_GRACE_SECONDS,
    MAX_SIGNALING_FRAME_LENGTH,
    NORMAL_CLOSE_CODES,
    PEER_ID_BYTES,
    POLICY_VIOLATION_CLOSE_CODE,
    RESUME_TOKEN_BYTES,
    RESUME_WINDOW_SECONDS,
    SEAT_CLOSED_BY_SERVER_CODE,
    SERVER_ERROR_CLOSE_CODE,
    SESSION_ENDED_CLOSE_CODE,
    SESSION_RECHECK_SECONDS,
    WEBSOCKET_DISCONNECT_EVENT,
    ResumeRefusalCause,
    RoomClosedReason,
    SignalType,
)
from hopin.errors import HopInError, InvalidMessageError, ResumeRefusedError, RoomNotFoundError
from hopin.signaling.replay import SEQUENCE_KEY, ReplayLog

if TYPE_CHECKING:
    from hopin.rooms.manager import RoomManager
    from hopin.rooms.models import Account
    from hopin.signaling.identity import IdentityResolver
    from hopin.signaling.scheduler import Scheduler, TimerHandle

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


PeerId = NewType("PeerId", str)


@dataclass(frozen=True)
class Park:
    generation: int
    dropped_at: float
    timer: TimerHandle


@dataclass(frozen=True)
class HostGrace:
    host_peer_id: str
    deadline: float
    timer: TimerHandle


def _mint_peer_id() -> PeerId:
    return PeerId(secrets.token_urlsafe(PEER_ID_BYTES))


def _mint_resume_token() -> str:
    return secrets.token_urlsafe(RESUME_TOKEN_BYTES)


@dataclass
class Peer:
    """A seat: the account behind it, the socket that currently serves it and what a resume needs."""

    account: Account
    websocket: WebSocket
    resume_token: str = field(default_factory=_mint_resume_token, repr=False)
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


@dataclass(frozen=True)
class ResumeClaim:
    participant_id: PeerId
    token: str
    last_seq: int
    sent_floor: int

    @classmethod
    def from_frame(cls, data: dict[str, Any]) -> ResumeClaim:
        participant_id = data.get("participant_id")
        token = data.get("token")
        last_seq = data.get("last_seq")
        sent_floor = data.get("sent_floor")
        if not (isinstance(participant_id, str) and isinstance(token, str)):
            raise ResumeRefusedError(ResumeRefusalCause.MALFORMED)
        if not (_is_sequence_number(last_seq) and _is_sequence_number(sent_floor)):
            raise ResumeRefusedError(ResumeRefusalCause.MALFORMED)
        return cls(PeerId(participant_id), token, last_seq, sent_floor)


def _token_matches(claimed: str, expected: str) -> bool:
    try:
        claimed_bytes = claimed.encode()
    except UnicodeEncodeError:
        return False
    return secrets.compare_digest(claimed_bytes, expected.encode())


def _is_sequence_number(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class SignalingHandler:
    def __init__(self, room_manager: RoomManager, identity_resolver: IdentityResolver, scheduler: Scheduler) -> None:
        self._room_manager = room_manager
        self._identity_resolver = identity_resolver
        self._scheduler = scheduler
        self._graces: dict[str, HostGrace] = {}
        self._peers: dict[PeerId, Peer] = {}
        self._peer_rooms: dict[str, str] = {}

    async def handle_websocket(self, websocket: WebSocket) -> None:
        account = await self._identity_resolver.resolve(websocket)
        if account is None:
            await websocket.close(code=POLICY_VIOLATION_CLOSE_CODE)
            return

        await websocket.accept()
        peer_id = _mint_peer_id()
        self._peers[peer_id] = Peer(account=account, websocket=websocket)
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
                if frame["code"] not in NORMAL_CLOSE_CODES and not _closed_by_server(connection.websocket):
                    connection.dropped = True
                    await logger.aerror("websocket_error", peer_id=connection.peer_id, code=frame["code"])
                return
            text = frame.get("text")
            if text is not None:
                if not await self._session_admits(connection) or not self._serves_seat(connection):
                    return
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
        peer = self._peers.get(connection.peer_id)
        return peer is not None and peer.websocket is connection.websocket

    def _serves_seat(self, connection: Connection) -> bool:
        return not connection.ended and self._holds_seat(connection)

    async def _end_session(self, connection: Connection, close_code: int) -> None:
        # A check can be suspended across a drop or a takeover; the seat then belongs to someone else.
        if not self._serves_seat(connection):
            return
        peer_id = connection.peer_id
        connection.ended = True
        await logger.ainfo("session_ended", peer_id=peer_id)
        if not self._holds_seat(connection):
            return
        await self._handle_disconnect(peer_id)
        await _close_socket(connection.websocket, close_code)

    async def _end_connection(self, connection: Connection) -> None:
        connection.ended = True
        connection.recheck.cancel()
        if not self._holds_seat(connection):
            return
        if connection.dropped and connection.peer_id in self._peer_rooms:
            await self._park(connection.peer_id, self._peers[connection.peer_id])
        else:
            await self._handle_disconnect(connection.peer_id)

    async def _handle_message(self, connection: Connection, raw_data: str) -> None:
        peer_id = connection.peer_id
        if not self._holds_seat(connection):
            return
        peer = self._peers[peer_id]
        is_first_frame = connection.frames_received == 0
        connection.frames_received += 1

        if len(raw_data) > MAX_SIGNALING_FRAME_LENGTH:
            await self._send_error(peer_id, InvalidMessageError("Message too long"))
            return

        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError:
            await self._send_error(peer_id, InvalidMessageError("Invalid JSON"))
            return

        if not isinstance(data, dict):
            await self._send_error(peer_id, InvalidMessageError("Message must be a JSON object"))
            return

        signal_type = data.get("type")
        if not isinstance(signal_type, str):
            await self._send_error(peer_id, InvalidMessageError("Missing signal type"))
            return

        if signal_type == SignalType.RESUME.value:
            await self._handle_resume(connection, data, is_first_frame=is_first_frame)
            return

        if not is_first_frame:
            seq = data.get(SEQUENCE_KEY)
            if not _is_sequence_number(seq):
                await self._send_error(peer_id, InvalidMessageError("Missing sequence number"))
                return
            if seq <= peer.received_seq:
                return
            peer.received_seq = seq

        handlers: dict[str, Any] = {
            SignalType.JOIN.value: self._handle_join,
            SignalType.OFFER.value: self._handle_relay,
            SignalType.ANSWER.value: self._handle_relay,
            SignalType.ICE_CANDIDATE.value: self._handle_relay,
            SignalType.LOCK_ROOM.value: self._handle_lock,
            SignalType.UNLOCK_ROOM.value: self._handle_unlock,
            SignalType.CHAT.value: self._handle_chat,
            SignalType.MEDIA_STATE.value: self._handle_media_state,
            SignalType.END_ROOM.value: self._handle_end_room,
        }

        handler = handlers.get(signal_type)
        if handler is None:
            await self._send_error(peer_id, InvalidMessageError(f"Unknown signal type: {signal_type}"))
            return

        try:
            await handler(peer_id, data)
        except HopInError as exc:
            await self._send_error(peer_id, exc)

    async def _handle_join(self, peer_id: PeerId, data: dict[str, Any]) -> None:
        room_id = data.get("room_id")
        if room_id is not None and not isinstance(room_id, str):
            raise InvalidMessageError("room_id must be a string")
        if peer_id in self._peer_rooms:
            raise InvalidMessageError("This seat is already in a room")

        if room_id is None:
            await self._create_room(peer_id)
        else:
            await self._join_room(peer_id, room_id)

    async def _create_room(self, peer_id: PeerId) -> None:
        room = self._room_manager.create_room(peer_id, self._account_of(peer_id))
        self._peer_rooms[peer_id] = room.room_id
        await self._send(
            peer_id,
            {
                "type": SignalType.ROOM_CREATED.value,
                "room_id": room.room_id,
                "is_host": True,
                **self._seat_credentials(peer_id),
            },
        )
        await logger.ainfo("room_created", room_id=room.room_id, host=peer_id)

    async def _join_room(self, peer_id: PeerId, room_id: str) -> None:
        account = self._account_of(peer_id)
        room = self._room_manager.add_participant(room_id, peer_id, account)
        self._peer_rooms[peer_id] = room_id
        host_returned = room.is_hosted_by(account) and self._cancel_grace(room_id)
        grace = self._graces.get(room_id)
        others = [pid for pid in room.participant_ids if pid != peer_id]

        await self._send(
            peer_id,
            {
                "type": SignalType.EXISTING_PARTICIPANTS.value,
                "participants": others,
                "names": {pid: self._account_of(pid).name for pid in others},
                "is_host": room.is_hosted_by(account),
                "locked": room.locked,
                **self._seat_credentials(peer_id),
            },
        )
        if grace is not None:
            await self._send(peer_id, self._host_away_frame(grace))
        await self._send_all(
            others,
            {"type": SignalType.PARTICIPANT_JOINED.value, "participant_id": peer_id, "name": account.name},
        )
        if host_returned:
            await self._send_all(others, {"type": SignalType.HOST_BACK.value, "participant_id": peer_id})

        await logger.ainfo("participant_joined", room_id=room_id, participant=peer_id)

    async def _handle_lock(self, peer_id: PeerId, _data: dict[str, Any]) -> None:
        await self._set_locked(peer_id, locked=True)

    async def _handle_unlock(self, peer_id: PeerId, _data: dict[str, Any]) -> None:
        await self._set_locked(peer_id, locked=False)

    async def _set_locked(self, peer_id: PeerId, *, locked: bool) -> None:
        room_id = self._peer_rooms.get(peer_id)
        if not room_id:
            return
        room = self._room_manager.set_room_locked(room_id, self._account_of(peer_id), locked=locked)
        signal_type = SignalType.ROOM_LOCKED if locked else SignalType.ROOM_UNLOCKED
        await self._broadcast_to_room(room_id, {"type": signal_type.value})
        await logger.ainfo(signal_type.value, room_id=room.room_id, by=peer_id)

    async def _handle_end_room(self, peer_id: PeerId, _data: dict[str, Any]) -> None:
        room_id = self._peer_rooms.get(peer_id)
        if not room_id:
            return
        self._room_manager.get_room_for_host(room_id, self._account_of(peer_id))
        await self._end_room(room_id, RoomClosedReason.ENDED)

    async def _handle_chat(self, peer_id: PeerId, data: dict[str, Any]) -> None:
        room_id = self._peer_rooms.get(peer_id)
        if not room_id:
            return
        message = data.get("message", "")
        if not message:
            return
        room = self._room_manager.get_room(room_id)
        others = [pid for pid in room.participant_ids if pid != peer_id]
        await self._send_all(others, {"type": SignalType.CHAT.value, "message": message, "from": peer_id})

    async def _handle_media_state(self, peer_id: PeerId, data: dict[str, Any]) -> None:
        audio, video = data.get("audio"), data.get("video")
        if not isinstance(audio, bool) or not isinstance(video, bool):
            raise InvalidMessageError("media_state needs boolean audio and video")
        room_id = self._peer_rooms.get(peer_id)
        if not room_id:
            return
        room = self._room_manager.get_room(room_id)
        others = [pid for pid in room.participant_ids if pid != peer_id]
        await self._send_all(
            others,
            {"type": SignalType.MEDIA_STATE.value, "from": peer_id, "audio": audio, "video": video},
        )

    async def _handle_relay(self, peer_id: PeerId, data: dict[str, Any]) -> None:
        target_id = data.get("target")
        if target_id is None:
            return
        if not isinstance(target_id, str):
            raise InvalidMessageError("target must be a string")
        sender_room = self._peer_rooms.get(peer_id)
        if sender_room is None or self._peer_rooms.get(target_id) != sender_room:
            await logger.ainfo("relay_outside_room", peer_id=peer_id, target=target_id)
            return
        await self._send(
            target_id,
            {
                "type": data["type"],
                "sdp": data.get("sdp"),
                "candidate": data.get("candidate"),
                "from": peer_id,
            },
        )

    async def _handle_disconnect(self, peer_id: PeerId) -> None:
        peer = self._forget_seat(peer_id)
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
            await self._send_all(remaining, self._host_away_frame(grace))

        await self._send_all(remaining, {"type": SignalType.PARTICIPANT_LEFT.value, "participant_id": peer_id})
        await logger.ainfo("participant_left", room_id=room_id, participant=peer_id)

    def _forget_seat(self, peer_id: PeerId) -> Peer | None:
        peer = self._peers.pop(peer_id, None)
        if peer is not None and peer.park is not None:
            peer.park.timer.cancel()
        return peer

    async def _park(self, peer_id: PeerId, peer: Peer) -> None:
        peer.parks_started += 1
        generation = peer.parks_started
        timer = self._scheduler.call_later(RESUME_WINDOW_SECONDS, partial(self._expire_park, peer_id, generation))
        peer.park = Park(generation=generation, dropped_at=self._scheduler.now(), timer=timer)
        peer.replaying = False
        await logger.ainfo("seat_parked", peer_id=peer_id)

    async def _expire_park(self, peer_id: PeerId, generation: int) -> None:
        peer = self._peers.get(peer_id)
        if peer is not None and peer.park is not None and peer.park.generation == generation:
            await self._handle_disconnect(peer_id)

    async def _handle_resume(self, connection: Connection, data: dict[str, Any], *, is_first_frame: bool) -> None:
        try:
            if not is_first_frame:
                raise ResumeRefusedError(ResumeRefusalCause.NOT_FIRST_FRAME)
            claim = ResumeClaim.from_frame(data)
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
        seat = self._peers.get(claim.participant_id)
        if seat is None:
            raise ResumeRefusedError(ResumeRefusalCause.UNKNOWN_SEAT)
        if not _token_matches(claim.token, seat.resume_token):
            raise ResumeRefusedError(ResumeRefusalCause.CREDENTIAL_MISMATCH)
        if self._account_of(connection.peer_id).id != seat.account.id:
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
        seat = self._peers[peer_id]
        await self._handle_disconnect(peer_id)
        await _close_socket(seat.websocket)

    async def _refuse_resume(self, connection: Connection, refusal: ResumeRefusedError) -> None:
        await logger.ainfo("resume_refused", peer_id=connection.peer_id, cause=refusal.cause.value)
        await self._write(
            connection.websocket,
            {"type": SignalType.ERROR.value, "reason": refusal.reason.value, "message": str(refusal)},
        )

    async def _take_over_seat(self, connection: Connection, seat: Peer, claim: ResumeClaim) -> None:
        superseded = seat.websocket
        if seat.park is not None:
            seat.park.timer.cancel()
            seat.park = None
        self._forget_seat(connection.peer_id)
        connection.peer_id = claim.participant_id
        seat.websocket = connection.websocket
        seat.resume_token = _mint_resume_token()
        seat.replaying = True

        await _close_socket(superseded)
        await self._write(
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
        while self._peers.get(peer_id) is seat and seat.websocket is websocket:
            if not seat.log.reaches_back_to(cursor):
                await self._release_unresumable_seat(peer_id)
                return
            pending = seat.log.since(cursor)
            if not pending:
                seat.replaying = False
                return
            if not await self._write(websocket, pending[0]):
                return
            cursor = pending[0][SEQUENCE_KEY]

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

        frame: dict[str, Any] = {"type": SignalType.ROOM_CLOSED.value, "reason": reason.value}
        if reason is RoomClosedReason.HOST_GONE:
            frame["grace_seconds"] = HOST_GRACE_SECONDS
        await self._send_all(recipients, frame)
        seats = [self._forget_seat(PeerId(peer_id)) for peer_id in recipients]
        for websocket in [seat.websocket for seat in seats if seat is not None]:
            await _close_socket(websocket)
        await logger.ainfo("room_closed", room_id=room_id, reason=reason.value)

    def _host_away_frame(self, grace: HostGrace) -> dict[str, Any]:
        return {
            "type": SignalType.HOST_AWAY.value,
            "seconds_left": math.ceil(grace.deadline - self._scheduler.now()),
            "participant_id": grace.host_peer_id,
        }

    def _account_of(self, peer_id: str) -> Account:
        return self._peers[PeerId(peer_id)].account

    async def _broadcast_to_room(self, room_id: str, data: dict[str, Any]) -> None:
        await self._send_all(self._room_manager.get_room(room_id).participant_ids, data)

    async def _send_all(self, peer_ids: list[str], data: dict[str, Any]) -> None:
        for peer_id in peer_ids:
            await self._send(peer_id, data)

    async def _send(self, peer_id: str, data: dict[str, Any]) -> None:
        # The browser drops frames at or below the last seq it dispatched, so nothing may await
        # between assigning a frame's seq in the log and writing it.
        peer = self._peers.get(PeerId(peer_id))
        if peer is None:
            return
        frame = peer.log.append(data)
        if peer.replaying or peer.park is not None:
            return
        await self._write(peer.websocket, frame, peer_id)

    @staticmethod
    async def _write(websocket: WebSocket, frame: dict[str, Any], peer_id: str | None = None) -> bool:
        if not _is_connected(websocket):
            return False
        try:
            await websocket.send_text(json.dumps(frame))
        except WebSocketDisconnect:
            await logger.ainfo("send_to_gone_peer", peer_id=peer_id)
            return False
        return True

    def _seat_credentials(self, peer_id: PeerId) -> dict[str, str]:
        return {"participant_id": peer_id, "resume_token": self._peers[peer_id].resume_token}

    async def _send_error(self, peer_id: PeerId, error: HopInError) -> None:
        await self._send(
            peer_id,
            {
                "type": SignalType.ERROR.value,
                "reason": error.reason.value,
                "message": str(error),
                **error.frame_fields(),
            },
        )


async def _close_socket(websocket: WebSocket, code: int = SEAT_CLOSED_BY_SERVER_CODE) -> None:
    if not _is_connected(websocket):
        return
    try:
        await websocket.close(code=code)
    except WebSocketDisconnect:
        await logger.ainfo("closed_socket_already_gone")


def _closed_by_server(websocket: WebSocket) -> bool:
    return websocket.application_state == WebSocketState.DISCONNECTED


def _is_connected(websocket: WebSocket) -> bool:
    return (
        websocket.client_state == WebSocketState.CONNECTED and websocket.application_state == WebSocketState.CONNECTED
    )
