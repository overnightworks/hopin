import json
from unittest.mock import AsyncMock

import pytest

from openstream.config.constants import RoomRole, SignalType
from openstream.rooms.manager import RoomManager
from openstream.signaling.handler import SignalingHandler


@pytest.fixture
def room_manager():
    return RoomManager(max_viewers_per_room=5)


@pytest.fixture
def handler(room_manager):
    return SignalingHandler(room_manager)


def make_ws_mock():
    ws = AsyncMock()
    ws.closed = False
    ws.send_str = AsyncMock()
    return ws


def register_connection(handler, connection_id):
    ws = make_ws_mock()
    handler._connections[connection_id] = ws
    return ws


class TestHandleMessage:
    @pytest.mark.asyncio
    async def test_invalid_json_sends_error(self, handler):
        ws = register_connection(handler, "c1")
        await handler._handle_message("c1", "not json")
        ws.send_str.assert_called_once()
        response = json.loads(ws.send_str.call_args[0][0])
        assert response["type"] == SignalType.ERROR.value

    @pytest.mark.asyncio
    async def test_missing_type_sends_error(self, handler):
        ws = register_connection(handler, "c1")
        await handler._handle_message("c1", json.dumps({"foo": "bar"}))
        response = json.loads(ws.send_str.call_args[0][0])
        assert response["type"] == SignalType.ERROR.value
        assert "Missing" in response["message"]

    @pytest.mark.asyncio
    async def test_unknown_type_sends_error(self, handler):
        ws = register_connection(handler, "c1")
        await handler._handle_message("c1", json.dumps({"type": "unknown"}))
        response = json.loads(ws.send_str.call_args[0][0])
        assert response["type"] == SignalType.ERROR.value
        assert "Unknown" in response["message"]


class TestJoinAsStreamer:
    @pytest.mark.asyncio
    async def test_creates_room_and_notifies(self, handler):
        ws = register_connection(handler, "streamer1")
        await handler._handle_message(
            "streamer1",
            json.dumps({"type": SignalType.JOIN.value, "role": RoomRole.STREAMER.value}),
        )
        response = json.loads(ws.send_str.call_args[0][0])
        assert response["type"] == SignalType.ROOM_CREATED.value
        assert "room_id" in response

    @pytest.mark.asyncio
    async def test_registers_connection_room(self, handler):
        register_connection(handler, "streamer1")
        await handler._handle_message(
            "streamer1",
            json.dumps({"type": SignalType.JOIN.value, "role": RoomRole.STREAMER.value}),
        )
        assert "streamer1" in handler._connection_rooms
        _, role = handler._connection_rooms["streamer1"]
        assert role == RoomRole.STREAMER


class TestJoinAsViewer:
    @pytest.mark.asyncio
    async def test_joins_room_and_notifies_streamer(self, handler, room_manager):
        streamer_ws = register_connection(handler, "streamer1")
        register_connection(handler, "viewer1")

        room = room_manager.create_room("streamer1")
        handler._connection_rooms["streamer1"] = (room.room_id, RoomRole.STREAMER)

        await handler._handle_message(
            "viewer1",
            json.dumps(
                {
                    "type": SignalType.JOIN.value,
                    "role": RoomRole.VIEWER.value,
                    "room_id": room.room_id,
                }
            ),
        )

        response = json.loads(streamer_ws.send_str.call_args[0][0])
        assert response["type"] == SignalType.VIEWER_JOINED.value
        assert response["viewer_id"] == "viewer1"

    @pytest.mark.asyncio
    async def test_invalid_room_sends_error(self, handler):
        ws = register_connection(handler, "viewer1")
        await handler._handle_message(
            "viewer1",
            json.dumps(
                {
                    "type": SignalType.JOIN.value,
                    "role": RoomRole.VIEWER.value,
                    "room_id": "nonexistent",
                }
            ),
        )
        response = json.loads(ws.send_str.call_args[0][0])
        assert response["type"] == SignalType.ERROR.value

    @pytest.mark.asyncio
    async def test_invalid_join_request_sends_error(self, handler):
        ws = register_connection(handler, "c1")
        await handler._handle_message(
            "c1",
            json.dumps({"type": SignalType.JOIN.value, "role": "invalid"}),
        )
        response = json.loads(ws.send_str.call_args[0][0])
        assert response["type"] == SignalType.ERROR.value
        assert "Invalid" in response["message"]


class TestRelayMessages:
    @pytest.mark.asyncio
    async def test_offer_relayed_to_target(self, handler):
        register_connection(handler, "streamer1")
        viewer_ws = register_connection(handler, "viewer1")

        await handler._handle_message(
            "streamer1",
            json.dumps(
                {
                    "type": SignalType.OFFER.value,
                    "target": "viewer1",
                    "sdp": "test_sdp",
                }
            ),
        )

        response = json.loads(viewer_ws.send_str.call_args[0][0])
        assert response["type"] == SignalType.OFFER.value
        assert response["sdp"] == "test_sdp"
        assert response["from"] == "streamer1"

    @pytest.mark.asyncio
    async def test_answer_relayed_to_streamer(self, handler, room_manager):
        streamer_ws = register_connection(handler, "streamer1")
        register_connection(handler, "viewer1")

        room = room_manager.create_room("streamer1")
        handler._connection_rooms["streamer1"] = (room.room_id, RoomRole.STREAMER)
        handler._connection_rooms["viewer1"] = (room.room_id, RoomRole.VIEWER)
        room_manager.add_viewer(room.room_id, "viewer1")

        await handler._handle_message(
            "viewer1",
            json.dumps({"type": SignalType.ANSWER.value, "sdp": "answer_sdp"}),
        )

        response = json.loads(streamer_ws.send_str.call_args[0][0])
        assert response["type"] == SignalType.ANSWER.value
        assert response["sdp"] == "answer_sdp"
        assert response["from"] == "viewer1"

    @pytest.mark.asyncio
    async def test_offer_without_target_is_ignored(self, handler):
        register_connection(handler, "streamer1")
        await handler._handle_message(
            "streamer1",
            json.dumps({"type": SignalType.OFFER.value, "sdp": "test_sdp"}),
        )

    @pytest.mark.asyncio
    async def test_answer_without_room_is_ignored(self, handler):
        register_connection(handler, "viewer1")
        await handler._handle_message(
            "viewer1",
            json.dumps({"type": SignalType.ANSWER.value, "sdp": "answer_sdp"}),
        )


class TestIceCandidate:
    @pytest.mark.asyncio
    async def test_ice_candidate_with_target(self, handler):
        register_connection(handler, "c1")
        target_ws = register_connection(handler, "c2")

        await handler._handle_message(
            "c1",
            json.dumps(
                {
                    "type": SignalType.ICE_CANDIDATE.value,
                    "target": "c2",
                    "candidate": "test_candidate",
                }
            ),
        )

        response = json.loads(target_ws.send_str.call_args[0][0])
        assert response["type"] == SignalType.ICE_CANDIDATE.value
        assert response["candidate"] == "test_candidate"

    @pytest.mark.asyncio
    async def test_ice_candidate_viewer_to_streamer(self, handler, room_manager):
        streamer_ws = register_connection(handler, "streamer1")
        register_connection(handler, "viewer1")

        room = room_manager.create_room("streamer1")
        handler._connection_rooms["streamer1"] = (room.room_id, RoomRole.STREAMER)
        handler._connection_rooms["viewer1"] = (room.room_id, RoomRole.VIEWER)
        room_manager.add_viewer(room.room_id, "viewer1")

        await handler._handle_message(
            "viewer1",
            json.dumps(
                {
                    "type": SignalType.ICE_CANDIDATE.value,
                    "candidate": "viewer_candidate",
                }
            ),
        )

        response = json.loads(streamer_ws.send_str.call_args[0][0])
        assert response["type"] == SignalType.ICE_CANDIDATE.value
        assert response["from"] == "viewer1"

    @pytest.mark.asyncio
    async def test_ice_candidate_streamer_to_viewers(self, handler, room_manager):
        register_connection(handler, "streamer1")
        viewer_ws = register_connection(handler, "viewer1")

        room = room_manager.create_room("streamer1")
        handler._connection_rooms["streamer1"] = (room.room_id, RoomRole.STREAMER)
        handler._connection_rooms["viewer1"] = (room.room_id, RoomRole.VIEWER)
        room_manager.add_viewer(room.room_id, "viewer1")

        await handler._handle_message(
            "streamer1",
            json.dumps(
                {
                    "type": SignalType.ICE_CANDIDATE.value,
                    "candidate": "streamer_candidate",
                }
            ),
        )

        response = json.loads(viewer_ws.send_str.call_args[0][0])
        assert response["type"] == SignalType.ICE_CANDIDATE.value
        assert response["from"] == "streamer1"

    @pytest.mark.asyncio
    async def test_ice_candidate_no_room_ignored(self, handler):
        register_connection(handler, "c1")
        await handler._handle_message(
            "c1",
            json.dumps({"type": SignalType.ICE_CANDIDATE.value, "candidate": "test"}),
        )


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_streamer_disconnect_notifies_viewers(self, handler, room_manager):
        register_connection(handler, "streamer1")
        viewer_ws = register_connection(handler, "viewer1")

        room = room_manager.create_room("streamer1")
        handler._connection_rooms["streamer1"] = (room.room_id, RoomRole.STREAMER)
        handler._connection_rooms["viewer1"] = (room.room_id, RoomRole.VIEWER)
        room_manager.add_viewer(room.room_id, "viewer1")

        await handler._handle_disconnect("streamer1")

        response = json.loads(viewer_ws.send_str.call_args[0][0])
        assert response["type"] == SignalType.STREAMER_DISCONNECTED.value
        assert not room_manager.has_room(room.room_id)

    @pytest.mark.asyncio
    async def test_viewer_disconnect_notifies_streamer(self, handler, room_manager):
        streamer_ws = register_connection(handler, "streamer1")
        register_connection(handler, "viewer1")

        room = room_manager.create_room("streamer1")
        handler._connection_rooms["streamer1"] = (room.room_id, RoomRole.STREAMER)
        handler._connection_rooms["viewer1"] = (room.room_id, RoomRole.VIEWER)
        room_manager.add_viewer(room.room_id, "viewer1")

        await handler._handle_disconnect("viewer1")

        response = json.loads(streamer_ws.send_str.call_args[0][0])
        assert response["type"] == SignalType.VIEWER_LEFT.value
        assert response["viewer_id"] == "viewer1"

    @pytest.mark.asyncio
    async def test_disconnect_without_room_is_safe(self, handler):
        register_connection(handler, "c1")
        await handler._handle_disconnect("c1")
        assert "c1" not in handler._connections

    @pytest.mark.asyncio
    async def test_viewer_disconnect_from_deleted_room(self, handler, room_manager):
        register_connection(handler, "viewer1")
        handler._connection_rooms["viewer1"] = ("deleted_room", RoomRole.VIEWER)
        await handler._handle_disconnect("viewer1")
        assert "viewer1" not in handler._connection_rooms


class TestSend:
    @pytest.mark.asyncio
    async def test_send_to_connected_client(self, handler):
        ws = register_connection(handler, "c1")
        await handler._send("c1", {"type": "test"})
        ws.send_str.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_to_unknown_client_is_safe(self, handler):
        await handler._send("unknown", {"type": "test"})

    @pytest.mark.asyncio
    async def test_send_to_closed_ws_is_safe(self, handler):
        ws = register_connection(handler, "c1")
        ws.closed = True
        await handler._send("c1", {"type": "test"})
        ws.send_str.assert_not_called()
