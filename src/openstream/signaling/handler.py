import json
from typing import Any

import structlog
from aiohttp import WSMsgType, web

from openstream.config.constants import RoomRole, SignalType
from openstream.errors import OpenStreamError, RoomNotFoundError
from openstream.rooms.manager import RoomManager

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class SignalingHandler:
    def __init__(self, room_manager: RoomManager) -> None:
        self._room_manager = room_manager
        self._connections: dict[str, web.WebSocketResponse] = {}
        self._connection_rooms: dict[str, tuple[str, RoomRole]] = {}

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
            SignalType.OFFER.value: self._handle_relay_to_viewer,
            SignalType.ANSWER.value: self._handle_relay_to_streamer,
            SignalType.ICE_CANDIDATE.value: self._handle_ice_candidate,
        }

        handler = handlers.get(signal_type)
        if handler is None:
            await self._send_error(connection_id, f"Unknown signal type: {signal_type}")
            return

        try:
            await handler(connection_id, data)
        except OpenStreamError as exc:
            await self._send_error(connection_id, str(exc))

    async def _handle_join(self, connection_id: str, data: dict[str, Any]) -> None:
        role = data.get("role")
        room_id = data.get("room_id")

        if role == RoomRole.STREAMER.value:
            room = self._room_manager.create_room(connection_id)
            self._connection_rooms[connection_id] = (room.room_id, RoomRole.STREAMER)
            await self._send(
                connection_id,
                {
                    "type": SignalType.ROOM_CREATED.value,
                    "room_id": room.room_id,
                },
            )
            await logger.ainfo("room_created", room_id=room.room_id, streamer=connection_id)

        elif role == RoomRole.VIEWER.value and room_id:
            room = self._room_manager.add_viewer(room_id, connection_id)
            self._connection_rooms[connection_id] = (room_id, RoomRole.VIEWER)
            await self._send(
                room.streamer_id,
                {
                    "type": SignalType.VIEWER_JOINED.value,
                    "viewer_id": connection_id,
                },
            )
            await logger.ainfo("viewer_joined", room_id=room_id, viewer=connection_id)

        else:
            await self._send_error(connection_id, "Invalid join request")

    async def _handle_relay_to_viewer(self, connection_id: str, data: dict[str, Any]) -> None:
        target_id = data.get("target")
        if not target_id:
            return
        await self._send(
            target_id,
            {
                "type": data["type"],
                "sdp": data.get("sdp"),
                "from": connection_id,
            },
        )

    async def _handle_relay_to_streamer(self, connection_id: str, data: dict[str, Any]) -> None:
        room_info = self._connection_rooms.get(connection_id)
        if not room_info:
            return
        room_id, _ = room_info
        try:
            room = self._room_manager.get_room(room_id)
        except RoomNotFoundError:
            return
        await self._send(
            room.streamer_id,
            {
                "type": data["type"],
                "sdp": data.get("sdp"),
                "from": connection_id,
            },
        )

    async def _handle_ice_candidate(self, connection_id: str, data: dict[str, Any]) -> None:
        target_id = data.get("target")
        if target_id:
            await self._send(
                target_id,
                {
                    "type": SignalType.ICE_CANDIDATE.value,
                    "candidate": data.get("candidate"),
                    "from": connection_id,
                },
            )
            return

        room_info = self._connection_rooms.get(connection_id)
        if not room_info:
            return
        room_id, role = room_info
        try:
            room = self._room_manager.get_room(room_id)
        except RoomNotFoundError:
            return

        if role == RoomRole.VIEWER:
            await self._send(
                room.streamer_id,
                {
                    "type": SignalType.ICE_CANDIDATE.value,
                    "candidate": data.get("candidate"),
                    "from": connection_id,
                },
            )
        elif role == RoomRole.STREAMER:
            for viewer_id in room.viewers:
                await self._send(
                    viewer_id,
                    {
                        "type": SignalType.ICE_CANDIDATE.value,
                        "candidate": data.get("candidate"),
                        "from": connection_id,
                    },
                )

    async def _handle_disconnect(self, connection_id: str) -> None:
        self._connections.pop(connection_id, None)
        room_info = self._connection_rooms.pop(connection_id, None)
        if not room_info:
            return

        room_id, role = room_info
        if role == RoomRole.STREAMER:
            try:
                room = self._room_manager.get_room(room_id)
            except RoomNotFoundError:
                return
            for viewer_id in list(room.viewers):
                await self._send(viewer_id, {"type": SignalType.STREAMER_DISCONNECTED.value})
                self._connection_rooms.pop(viewer_id, None)
            self._room_manager.remove_room(room_id)
            await logger.ainfo("room_closed", room_id=room_id)

        elif role == RoomRole.VIEWER:
            try:
                room = self._room_manager.get_room(room_id)
                self._room_manager.remove_viewer(room_id, connection_id)
                await self._send(
                    room.streamer_id,
                    {
                        "type": SignalType.VIEWER_LEFT.value,
                        "viewer_id": connection_id,
                    },
                )
            except RoomNotFoundError:
                pass
            await logger.ainfo("viewer_left", room_id=room_id, viewer=connection_id)

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
