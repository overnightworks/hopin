import json
import math
import secrets
from dataclasses import dataclass
from functools import partial
from typing import Any, NewType

import structlog
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from hopin.config.constants import (
    HOST_GRACE_SECONDS,
    NORMAL_CLOSE_CODES,
    PEER_ID_BYTES,
    POLICY_VIOLATION_CLOSE_CODE,
    WEBSOCKET_DISCONNECT_EVENT,
    RoomClosedReason,
    SignalType,
)
from hopin.errors import HopInError, InvalidMessageError, RoomNotFoundError
from hopin.rooms.manager import RoomManager
from hopin.rooms.models import Account
from hopin.signaling.identity import IdentityResolver
from hopin.signaling.scheduler import Scheduler, TimerHandle

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


PeerId = NewType("PeerId", str)


@dataclass(frozen=True)
class Peer:
    account: Account
    websocket: WebSocket


@dataclass(frozen=True)
class HostGrace:
    host_peer_id: str
    deadline: float
    timer: TimerHandle


def _mint_peer_id() -> PeerId:
    return PeerId(secrets.token_urlsafe(PEER_ID_BYTES))


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
        await logger.ainfo("client_connected", peer_id=peer_id, account=account.id)

        try:
            await self._process_messages(websocket, peer_id)
        finally:
            await self._handle_disconnect(peer_id)

    async def _process_messages(self, websocket: WebSocket, peer_id: PeerId) -> None:
        while True:
            frame = await websocket.receive()
            if frame["type"] == WEBSOCKET_DISCONNECT_EVENT:
                if frame["code"] not in NORMAL_CLOSE_CODES:
                    await logger.aerror("websocket_error", peer_id=peer_id, code=frame["code"])
                return
            text = frame.get("text")
            if text is not None:
                await self._handle_message(peer_id, text)

    async def _handle_message(self, peer_id: PeerId, raw_data: str) -> None:
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

        handlers: dict[str, Any] = {
            SignalType.JOIN.value: self._handle_join,
            SignalType.OFFER.value: self._handle_relay,
            SignalType.ANSWER.value: self._handle_relay,
            SignalType.ICE_CANDIDATE.value: self._handle_relay,
            SignalType.LOCK_ROOM.value: self._handle_lock,
            SignalType.UNLOCK_ROOM.value: self._handle_unlock,
            SignalType.CHAT.value: self._handle_chat,
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
                "is_host": room.is_hosted_by(account),
                "locked": room.locked,
            },
        )
        if grace is not None:
            await self._send(peer_id, self._host_away_frame(grace))
        await self._send_all(others, {"type": SignalType.PARTICIPANT_JOINED.value, "participant_id": peer_id})
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
        self._peers.pop(peer_id, None)
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
            grace = self._start_grace(room_id, peer_id)
            await self._send_all(remaining, self._host_away_frame(grace))

        await self._send_all(remaining, {"type": SignalType.PARTICIPANT_LEFT.value, "participant_id": peer_id})
        await logger.ainfo("participant_left", room_id=room_id, participant=peer_id)

    def _start_grace(self, room_id: str, host_peer_id: str) -> HostGrace:
        timer = self._scheduler.call_later(HOST_GRACE_SECONDS, partial(self._expire_grace, room_id))
        grace = HostGrace(
            host_peer_id=host_peer_id,
            deadline=self._scheduler.now() + HOST_GRACE_SECONDS,
            timer=timer,
        )
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

        await self._send_all(recipients, {"type": SignalType.ROOM_CLOSED.value, "reason": reason.value})
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
        peer = self._peers.get(PeerId(peer_id))
        if peer is None or not _is_connected(peer.websocket):
            return
        try:
            await peer.websocket.send_text(json.dumps(data))
        except WebSocketDisconnect:
            await logger.ainfo("send_to_gone_peer", peer_id=peer_id)

    async def _send_error(self, peer_id: PeerId, error: HopInError) -> None:
        await self._send(
            peer_id,
            {
                "type": SignalType.ERROR.value,
                "reason": error.reason.value,
                "message": str(error),
            },
        )


def _is_connected(websocket: WebSocket) -> bool:
    return (
        websocket.client_state == WebSocketState.CONNECTED and websocket.application_state == WebSocketState.CONNECTED
    )
