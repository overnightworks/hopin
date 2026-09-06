import json
from typing import Any

import structlog
from aiohttp import WSMsgType, web

from hopin.config.constants import SignalType
from hopin.errors import HopInError, RoomNotFoundError
from hopin.rooms.manager import RoomManager

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class SignalingHandler:
    def __init__(self, room_manager: RoomManager) -> None:
        self._room_manager = room_manager
        self._connections: dict[str, web.WebSocketResponse] = {}
        self._connection_rooms: dict[str, str] = {}

    async def handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        connection_id = request.query.get("id", "")
        if not connection_id:
            await ws.close()
            return ws

        self._connections[connection_id] = ws
        await logger.ainfo("client_connected", connection_id=connection_id)

        try:
            await self._process_messages(ws, connection_id)
        finally:
            await self._handle_disconnect(connection_id)

        return ws

    async def _process_messages(self, ws: web.WebSocketResponse, connection_id: str) -> None:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                await self._handle_message(connection_id, msg.data)
            elif msg.type == WSMsgType.ERROR:
                await logger.aerror("websocket_error", connection_id=connection_id, error=ws.exception())

    async def _handle_message(self, connection_id: str, raw_data: str) -> None:
        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError:
            await self._send_error(connection_id, "Invalid JSON")
            return

        signal_type = data.get("type")
        if signal_type is None:
            await self._send_error(connection_id, "Missing signal type")
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
            await self._send_error(connection_id, f"Unknown signal type: {signal_type}")
            return

        try:
            await handler(connection_id, data)
        except HopInError as exc:
            await self._send_error(connection_id, str(exc))

    async def _handle_join(self, connection_id: str, data: dict[str, Any]) -> None:
        room_id = data.get("room_id")

        if room_id is None:
            password = data.get("password") or None
            room = self._room_manager.create_room(connection_id, password=password)
            self._connection_rooms[connection_id] = room.room_id
            await self._send(
                connection_id,
                {
                    "type": SignalType.ROOM_CREATED.value,
                    "room_id": room.room_id,
                    "is_host": True,
                },
            )
            await logger.ainfo("room_created", room_id=room.room_id, host=connection_id)
            return

        password = data.get("password")
        room = self._room_manager.add_participant(room_id, connection_id, password=password)
        self._connection_rooms[connection_id] = room_id

        existing_ids = [pid for pid in room.participant_ids if pid != connection_id]
        await self._send(
            connection_id,
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
                    "participant_id": connection_id,
                },
            )

        await logger.ainfo("participant_joined", room_id=room_id, participant=connection_id)

    async def _handle_lock(self, connection_id: str, _data: dict[str, Any]) -> None:
        room_id = self._connection_rooms.get(connection_id)
        if not room_id:
            return
        room = self._room_manager.set_room_locked(room_id, connection_id, locked=True)
        await self._broadcast_to_room(
            room_id,
            {
                "type": SignalType.ROOM_LOCKED.value,
            },
        )
        await logger.ainfo("room_locked", room_id=room.room_id, by=connection_id)

    async def _handle_unlock(self, connection_id: str, _data: dict[str, Any]) -> None:
        room_id = self._connection_rooms.get(connection_id)
        if not room_id:
            return
        room = self._room_manager.set_room_locked(room_id, connection_id, locked=False)
        await self._broadcast_to_room(
            room_id,
            {
                "type": SignalType.ROOM_UNLOCKED.value,
            },
        )
        await logger.ainfo("room_unlocked", room_id=room.room_id, by=connection_id)

    async def _handle_chat(self, connection_id: str, data: dict[str, Any]) -> None:
        room_id = self._connection_rooms.get(connection_id)
        if not room_id:
            return
        message = data.get("message", "")
        if not message:
            return
        room = self._room_manager.get_room(room_id)
        for participant_id in room.participant_ids:
            if participant_id != connection_id:
                await self._send(
                    participant_id,
                    {
                        "type": SignalType.CHAT.value,
                        "message": message,
                        "from": connection_id,
                    },
                )

    async def _handle_relay(self, connection_id: str, data: dict[str, Any]) -> None:
        target_id = data.get("target")
        if not target_id:
            return
        await self._send(
            target_id,
            {
                "type": data["type"],
                "sdp": data.get("sdp"),
                "candidate": data.get("candidate"),
                "from": connection_id,
            },
        )

    async def _handle_disconnect(self, connection_id: str) -> None:
        self._connections.pop(connection_id, None)
        room_id = self._connection_rooms.pop(connection_id, None)
        if not room_id:
            return

        try:
            room = self._room_manager.get_room(room_id)
        except RoomNotFoundError:
            return

        self._room_manager.remove_participant(room_id, connection_id)

        if room.is_empty:
            self._room_manager.remove_room(room_id)
            await logger.ainfo("room_closed", room_id=room_id)
            return

        for participant_id in room.participant_ids:
            await self._send(
                participant_id,
                {
                    "type": SignalType.PARTICIPANT_LEFT.value,
                    "participant_id": connection_id,
                },
            )

        await logger.ainfo("participant_left", room_id=room_id, participant=connection_id)

    async def _broadcast_to_room(self, room_id: str, data: dict[str, Any]) -> None:
        room = self._room_manager.get_room(room_id)
        for participant_id in room.participant_ids:
            await self._send(participant_id, data)

    async def _send(self, connection_id: str, data: dict[str, Any]) -> None:
        ws = self._connections.get(connection_id)
        if ws and not ws.closed:
            await ws.send_str(json.dumps(data))

    async def _send_error(self, connection_id: str, message: str) -> None:
        await self._send(
            connection_id,
            {
                "type": SignalType.ERROR.value,
                "message": message,
            },
        )
