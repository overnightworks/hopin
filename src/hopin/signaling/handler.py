import json
import secrets
from dataclasses import dataclass
from typing import Any, NewType

import structlog
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from hopin.config.constants import (
    NORMAL_CLOSE_CODES,
    PEER_ID_BYTES,
    POLICY_VIOLATION_CLOSE_CODE,
    WEBSOCKET_DISCONNECT_EVENT,
    SignalType,
)
from hopin.errors import HopInError, InvalidMessageError, RoomNotFoundError
from hopin.rooms.manager import RoomManager
from hopin.signaling.identity import Account, IdentityResolver

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


PeerId = NewType("PeerId", str)


@dataclass(frozen=True)
class Peer:
    account: Account
    websocket: WebSocket


def _mint_peer_id() -> PeerId:
    return PeerId(secrets.token_urlsafe(PEER_ID_BYTES))


class SignalingHandler:
    def __init__(self, room_manager: RoomManager, identity_resolver: IdentityResolver) -> None:
        self._room_manager = room_manager
        self._identity_resolver = identity_resolver
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
            room = self._room_manager.create_room(peer_id)
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
            return

        room = self._room_manager.add_participant(room_id, peer_id)
        self._peer_rooms[peer_id] = room_id

        existing_ids = [pid for pid in room.participant_ids if pid != peer_id]
        await self._send(
            peer_id,
            {
                "type": SignalType.EXISTING_PARTICIPANTS.value,
                "participants": existing_ids,
                "is_host": False,
                "locked": room.locked,
            },
        )

        for participant_id in existing_ids:
            await self._send(
                participant_id,
                {
                    "type": SignalType.PARTICIPANT_JOINED.value,
                    "participant_id": peer_id,
                },
            )

        await logger.ainfo("participant_joined", room_id=room_id, participant=peer_id)

    async def _handle_lock(self, peer_id: PeerId, _data: dict[str, Any]) -> None:
        room_id = self._peer_rooms.get(peer_id)
        if not room_id:
            return
        room = self._room_manager.set_room_locked(room_id, peer_id, locked=True)
        await self._broadcast_to_room(
            room_id,
            {
                "type": SignalType.ROOM_LOCKED.value,
            },
        )
        await logger.ainfo("room_locked", room_id=room.room_id, by=peer_id)

    async def _handle_unlock(self, peer_id: PeerId, _data: dict[str, Any]) -> None:
        room_id = self._peer_rooms.get(peer_id)
        if not room_id:
            return
        room = self._room_manager.set_room_locked(room_id, peer_id, locked=False)
        await self._broadcast_to_room(
            room_id,
            {
                "type": SignalType.ROOM_UNLOCKED.value,
            },
        )
        await logger.ainfo("room_unlocked", room_id=room.room_id, by=peer_id)

    async def _handle_chat(self, peer_id: PeerId, data: dict[str, Any]) -> None:
        room_id = self._peer_rooms.get(peer_id)
        if not room_id:
            return
        message = data.get("message", "")
        if not message:
            return
        room = self._room_manager.get_room(room_id)
        for participant_id in room.participant_ids:
            if participant_id != peer_id:
                await self._send(
                    participant_id,
                    {
                        "type": SignalType.CHAT.value,
                        "message": message,
                        "from": peer_id,
                    },
                )

    async def _handle_relay(self, peer_id: PeerId, data: dict[str, Any]) -> None:
        target_id = data.get("target")
        if target_id is None:
            return
        if not isinstance(target_id, str):
            raise InvalidMessageError("target must be a string")
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

        self._room_manager.remove_participant(room_id, peer_id)

        if room.is_empty:
            self._room_manager.remove_room(room_id)
            await logger.ainfo("room_closed", room_id=room_id)
            return

        for participant_id in room.participant_ids:
            await self._send(
                participant_id,
                {
                    "type": SignalType.PARTICIPANT_LEFT.value,
                    "participant_id": peer_id,
                },
            )

        await logger.ainfo("participant_left", room_id=room_id, participant=peer_id)

    async def _broadcast_to_room(self, room_id: str, data: dict[str, Any]) -> None:
        room = self._room_manager.get_room(room_id)
        for participant_id in room.participant_ids:
            await self._send(participant_id, data)

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
