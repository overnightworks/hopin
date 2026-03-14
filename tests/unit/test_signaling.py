import json
from unittest.mock import AsyncMock

import pytest

from openstream.config.constants import SignalType
from openstream.rooms.manager import RoomManager
from openstream.signaling.handler import SignalingHandler


@pytest.fixture
def room_manager():
    return RoomManager(max_participants_per_room=5)


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


class TestJoinCreateRoom:
    @pytest.mark.asyncio
    async def test_creates_room_when_no_room_id(self, handler):
        ws = register_connection(handler, "host1")
        await handler._handle_message(
            "host1",
            json.dumps({"type": SignalType.JOIN.value}),
        )
        response = json.loads(ws.send_str.call_args[0][0])
        assert response["type"] == SignalType.ROOM_CREATED.value
        assert "room_id" in response

    @pytest.mark.asyncio
    async def test_registers_connection_to_room(self, handler):
        register_connection(handler, "host1")
        await handler._handle_message(
            "host1",
            json.dumps({"type": SignalType.JOIN.value}),
        )
        assert "host1" in handler._connection_rooms


class TestJoinExistingRoom:
    @pytest.mark.asyncio
    async def test_joins_room_and_gets_existing_participants(self, handler, room_manager):
        register_connection(handler, "host1")
        register_connection(handler, "p1")

        room = room_manager.create_room("host1")
        handler._connection_rooms["host1"] = room.room_id

        await handler._handle_message(
            "p1",
            json.dumps({"type": SignalType.JOIN.value, "room_id": room.room_id}),
        )

        p1_ws = handler._connections["p1"]
        calls = p1_ws.send_str.call_args_list
        existing_msg = json.loads(calls[0][0][0])
        assert existing_msg["type"] == SignalType.EXISTING_PARTICIPANTS.value
        assert "host1" in existing_msg["participants"]

    @pytest.mark.asyncio
    async def test_notifies_existing_participants(self, handler, room_manager):
        host_ws = register_connection(handler, "host1")
        register_connection(handler, "p1")

        room = room_manager.create_room("host1")
        handler._connection_rooms["host1"] = room.room_id

        await handler._handle_message(
            "p1",
            json.dumps({"type": SignalType.JOIN.value, "room_id": room.room_id}),
        )

        host_calls = host_ws.send_str.call_args_list
        joined_msg = json.loads(host_calls[0][0][0])
        assert joined_msg["type"] == SignalType.PARTICIPANT_JOINED.value
        assert joined_msg["participant_id"] == "p1"

    @pytest.mark.asyncio
    async def test_invalid_room_sends_error(self, handler):
        ws = register_connection(handler, "p1")
        await handler._handle_message(
            "p1",
            json.dumps({"type": SignalType.JOIN.value, "room_id": "nonexistent"}),
        )
        response = json.loads(ws.send_str.call_args[0][0])
        assert response["type"] == SignalType.ERROR.value

    @pytest.mark.asyncio
    async def test_full_room_sends_error(self, handler):
        manager = RoomManager(max_participants_per_room=2)
        handler._room_manager = manager
        room = manager.create_room("host1")
        register_connection(handler, "host1")
        handler._connection_rooms["host1"] = room.room_id
        manager.add_participant(room.room_id, "p1")

        ws = register_connection(handler, "p2")
        await handler._handle_message(
            "p2",
            json.dumps({"type": SignalType.JOIN.value, "room_id": room.room_id}),
        )
        response = json.loads(ws.send_str.call_args[0][0])
        assert response["type"] == SignalType.ERROR.value


class TestRelay:
    @pytest.mark.asyncio
    async def test_offer_relayed_to_target(self, handler):
        register_connection(handler, "p1")
        target_ws = register_connection(handler, "p2")

        await handler._handle_message(
            "p1",
            json.dumps(
                {
                    "type": SignalType.OFFER.value,
                    "target": "p2",
                    "sdp": "test_sdp",
                }
            ),
        )

        response = json.loads(target_ws.send_str.call_args[0][0])
        assert response["type"] == SignalType.OFFER.value
        assert response["sdp"] == "test_sdp"
        assert response["from"] == "p1"

    @pytest.mark.asyncio
    async def test_answer_relayed_to_target(self, handler):
        target_ws = register_connection(handler, "p1")
        register_connection(handler, "p2")

        await handler._handle_message(
            "p2",
            json.dumps(
                {
                    "type": SignalType.ANSWER.value,
                    "target": "p1",
                    "sdp": "answer_sdp",
                }
            ),
        )

        response = json.loads(target_ws.send_str.call_args[0][0])
        assert response["type"] == SignalType.ANSWER.value
        assert response["sdp"] == "answer_sdp"
        assert response["from"] == "p2"

    @pytest.mark.asyncio
    async def test_ice_candidate_relayed_to_target(self, handler):
        register_connection(handler, "p1")
        target_ws = register_connection(handler, "p2")

        await handler._handle_message(
            "p1",
            json.dumps(
                {
                    "type": SignalType.ICE_CANDIDATE.value,
                    "target": "p2",
                    "candidate": "test_candidate",
                }
            ),
        )

        response = json.loads(target_ws.send_str.call_args[0][0])
        assert response["type"] == SignalType.ICE_CANDIDATE.value
        assert response["candidate"] == "test_candidate"
        assert response["from"] == "p1"

    @pytest.mark.asyncio
    async def test_relay_without_target_is_ignored(self, handler):
        ws = register_connection(handler, "p1")
        await handler._handle_message(
            "p1",
            json.dumps({"type": SignalType.OFFER.value, "sdp": "test_sdp"}),
        )
        ws.send_str.assert_not_called()


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_participant_disconnect_notifies_others(self, handler, room_manager):
        host_ws = register_connection(handler, "host1")
        register_connection(handler, "p1")

        room = room_manager.create_room("host1")
        room_manager.add_participant(room.room_id, "p1")
        handler._connection_rooms["host1"] = room.room_id
        handler._connection_rooms["p1"] = room.room_id

        await handler._handle_disconnect("p1")

        response = json.loads(host_ws.send_str.call_args[0][0])
        assert response["type"] == SignalType.PARTICIPANT_LEFT.value
        assert response["participant_id"] == "p1"

    @pytest.mark.asyncio
    async def test_last_participant_removes_room(self, handler, room_manager):
        register_connection(handler, "host1")
        room = room_manager.create_room("host1")
        handler._connection_rooms["host1"] = room.room_id

        await handler._handle_disconnect("host1")
        assert not room_manager.has_room(room.room_id)

    @pytest.mark.asyncio
    async def test_disconnect_without_room_is_safe(self, handler):
        register_connection(handler, "c1")
        await handler._handle_disconnect("c1")
        assert "c1" not in handler._connections

    @pytest.mark.asyncio
    async def test_disconnect_from_deleted_room(self, handler):
        register_connection(handler, "p1")
        handler._connection_rooms["p1"] = "deleted_room"
        await handler._handle_disconnect("p1")
        assert "p1" not in handler._connection_rooms

    @pytest.mark.asyncio
    async def test_room_not_removed_while_others_remain(self, handler, room_manager):
        register_connection(handler, "host1")
        register_connection(handler, "p1")

        room = room_manager.create_room("host1")
        room_manager.add_participant(room.room_id, "p1")
        handler._connection_rooms["host1"] = room.room_id
        handler._connection_rooms["p1"] = room.room_id

        await handler._handle_disconnect("p1")
        assert room_manager.has_room(room.room_id)


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
