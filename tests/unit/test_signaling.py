import json
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest
import structlog
import structlog.testing
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from hopin.config.constants import WEBSOCKET_DISCONNECT_EVENT, ErrorReason, SignalType
from hopin.rooms.manager import RoomManager
from hopin.signaling.handler import Peer, SignalingHandler
from hopin.signaling.identity import Account

ROOM_LIMIT = 5


@pytest.fixture
def room_manager():
    return RoomManager(max_participants_per_room=ROOM_LIMIT)


class FixedIdentityResolver:
    def __init__(self, account):
        self._account = account

    async def resolve(self, _websocket):
        return self._account


class ResolverError(Exception):
    pass


class RaisingIdentityResolver:
    async def resolve(self, _websocket):
        raise ResolverError


ADMITTED_ACCOUNT = Account(id="c1", name="c1")


@pytest.fixture
def handler(room_manager):
    return SignalingHandler(room_manager, FixedIdentityResolver(ADMITTED_ACCOUNT))


def make_ws_mock():
    ws = AsyncMock()
    ws.client_state = WebSocketState.CONNECTED
    ws.application_state = WebSocketState.CONNECTED
    ws.send_text = AsyncMock()
    return ws


def register_peer(handler, peer_id):
    ws = make_ws_mock()
    handler._peers[peer_id] = Peer(account=Account(id=peer_id, name=peer_id), websocket=ws)
    return ws


def text_frame(data):
    return {"type": "websocket.receive", "text": json.dumps(data)}


def disconnect_frame(code):
    return {"type": WEBSOCKET_DISCONNECT_EVENT, "code": code}


@dataclass
class FakeSocket:
    """A minimal inbound socket, for driving the message loop without a live transport."""

    frames: list[dict]
    client_state: WebSocketState = WebSocketState.CONNECTED
    application_state: WebSocketState = WebSocketState.CONNECTED
    accepted: bool = False
    closed_with: int | None = None
    sent: list[dict] = field(default_factory=list)

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int) -> None:
        self.closed_with = code

    async def receive(self) -> dict:
        return self.frames.pop(0)

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


def join_unknown_room(_handler, _room_manager):
    return json.dumps({"type": SignalType.JOIN.value, "room_id": "nonexistent"})


def join_locked_room(_handler, room_manager):
    room = room_manager.create_room("host1")
    room_manager.set_room_locked(room.room_id, "host1", locked=True)
    return json.dumps({"type": SignalType.JOIN.value, "room_id": room.room_id})


def join_full_room(_handler, room_manager):
    room = room_manager.create_room("host1")
    for index in range(ROOM_LIMIT - 1):
        room_manager.add_participant(room.room_id, f"member{index}")
    return json.dumps({"type": SignalType.JOIN.value, "room_id": room.room_id})


def lock_as_participant(handler, room_manager):
    room = room_manager.create_room("host1")
    room_manager.add_participant(room.room_id, "sender")
    handler._peer_rooms["sender"] = room.room_id
    return json.dumps({"type": SignalType.LOCK_ROOM.value})


class TestHandleMessageRefusals:
    @pytest.mark.parametrize(
        ("reason", "arrange"),
        [
            pytest.param(ErrorReason.NOT_FOUND, join_unknown_room, id="join_unknown_room"),
            pytest.param(ErrorReason.LOCKED, join_locked_room, id="join_locked_room"),
            pytest.param(ErrorReason.FULL, join_full_room, id="join_full_room"),
            pytest.param(ErrorReason.NOT_HOST, lock_as_participant, id="lock_as_participant"),
            pytest.param(ErrorReason.INVALID_MESSAGE, lambda *_: "not json", id="not_json"),
            pytest.param(ErrorReason.INVALID_MESSAGE, lambda *_: "[]", id="json_array"),
            pytest.param(ErrorReason.INVALID_MESSAGE, lambda *_: json.dumps({"foo": "bar"}), id="no_type"),
            pytest.param(ErrorReason.INVALID_MESSAGE, lambda *_: json.dumps({"type": "unknown"}), id="unknown_type"),
            pytest.param(ErrorReason.INVALID_MESSAGE, lambda *_: json.dumps({"type": []}), id="non_string_type"),
            pytest.param(
                ErrorReason.INVALID_MESSAGE,
                lambda *_: json.dumps({"type": SignalType.OFFER.value, "target": []}),
                id="non_string_target",
            ),
            pytest.param(
                ErrorReason.INVALID_MESSAGE,
                lambda *_: json.dumps({"type": SignalType.JOIN.value, "room_id": {}}),
                id="non_string_room_id",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_handle_message_refused_request_sends_error_frame_with_reason(
        self, handler, room_manager, reason, arrange
    ):
        ws = register_peer(handler, "sender")
        raw_data = arrange(handler, room_manager)

        await handler._handle_message("sender", raw_data)

        ws.send_text.assert_called_once()
        response = json.loads(ws.send_text.call_args[0][0])
        assert response["type"] == SignalType.ERROR.value
        assert response["reason"] == reason.value
        assert response["message"]


class TestJoinCreateRoom:
    @pytest.mark.asyncio
    async def test_creates_room_when_no_room_id(self, handler):
        ws = register_peer(handler, "host1")
        await handler._handle_message(
            "host1",
            json.dumps({"type": SignalType.JOIN.value}),
        )
        response = json.loads(ws.send_text.call_args[0][0])
        assert response["type"] == SignalType.ROOM_CREATED.value
        assert "room_id" in response

    @pytest.mark.asyncio
    async def test_registers_connection_to_room(self, handler):
        register_peer(handler, "host1")
        await handler._handle_message(
            "host1",
            json.dumps({"type": SignalType.JOIN.value}),
        )
        assert "host1" in handler._peer_rooms


class TestJoinExistingRoom:
    @pytest.mark.asyncio
    async def test_joins_room_and_gets_existing_participants(self, handler, room_manager):
        register_peer(handler, "host1")
        register_peer(handler, "p1")

        room = room_manager.create_room("host1")
        handler._peer_rooms["host1"] = room.room_id

        await handler._handle_message(
            "p1",
            json.dumps({"type": SignalType.JOIN.value, "room_id": room.room_id}),
        )

        p1_ws = handler._peers["p1"].websocket
        calls = p1_ws.send_text.call_args_list
        existing_msg = json.loads(calls[0][0][0])
        assert existing_msg["type"] == SignalType.EXISTING_PARTICIPANTS.value
        assert "host1" in existing_msg["participants"]

    @pytest.mark.asyncio
    async def test_notifies_existing_participants(self, handler, room_manager):
        host_ws = register_peer(handler, "host1")
        register_peer(handler, "p1")

        room = room_manager.create_room("host1")
        handler._peer_rooms["host1"] = room.room_id

        await handler._handle_message(
            "p1",
            json.dumps({"type": SignalType.JOIN.value, "room_id": room.room_id}),
        )

        host_calls = host_ws.send_text.call_args_list
        joined_msg = json.loads(host_calls[0][0][0])
        assert joined_msg["type"] == SignalType.PARTICIPANT_JOINED.value
        assert joined_msg["participant_id"] == "p1"

    @pytest.mark.asyncio
    async def test_invalid_room_sends_error(self, handler):
        ws = register_peer(handler, "p1")
        await handler._handle_message(
            "p1",
            json.dumps({"type": SignalType.JOIN.value, "room_id": "nonexistent"}),
        )
        response = json.loads(ws.send_text.call_args[0][0])
        assert response["type"] == SignalType.ERROR.value

    @pytest.mark.asyncio
    async def test_full_room_sends_error(self, handler):
        manager = RoomManager(max_participants_per_room=2)
        handler._room_manager = manager
        room = manager.create_room("host1")
        register_peer(handler, "host1")
        handler._peer_rooms["host1"] = room.room_id
        manager.add_participant(room.room_id, "p1")

        ws = register_peer(handler, "p2")
        await handler._handle_message(
            "p2",
            json.dumps({"type": SignalType.JOIN.value, "room_id": room.room_id}),
        )
        response = json.loads(ws.send_text.call_args[0][0])
        assert response["type"] == SignalType.ERROR.value


class TestRelay:
    @pytest.mark.asyncio
    async def test_offer_relayed_to_target(self, handler):
        register_peer(handler, "p1")
        target_ws = register_peer(handler, "p2")

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

        response = json.loads(target_ws.send_text.call_args[0][0])
        assert response["type"] == SignalType.OFFER.value
        assert response["sdp"] == "test_sdp"
        assert response["from"] == "p1"

    @pytest.mark.asyncio
    async def test_answer_relayed_to_target(self, handler):
        target_ws = register_peer(handler, "p1")
        register_peer(handler, "p2")

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

        response = json.loads(target_ws.send_text.call_args[0][0])
        assert response["type"] == SignalType.ANSWER.value
        assert response["sdp"] == "answer_sdp"
        assert response["from"] == "p2"

    @pytest.mark.asyncio
    async def test_ice_candidate_relayed_to_target(self, handler):
        register_peer(handler, "p1")
        target_ws = register_peer(handler, "p2")

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

        response = json.loads(target_ws.send_text.call_args[0][0])
        assert response["type"] == SignalType.ICE_CANDIDATE.value
        assert response["candidate"] == "test_candidate"
        assert response["from"] == "p1"

    @pytest.mark.asyncio
    async def test_relay_without_target_is_ignored(self, handler):
        ws = register_peer(handler, "p1")
        await handler._handle_message(
            "p1",
            json.dumps({"type": SignalType.OFFER.value, "sdp": "test_sdp"}),
        )
        ws.send_text.assert_not_called()


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_participant_disconnect_notifies_others(self, handler, room_manager):
        host_ws = register_peer(handler, "host1")
        register_peer(handler, "p1")

        room = room_manager.create_room("host1")
        room_manager.add_participant(room.room_id, "p1")
        handler._peer_rooms["host1"] = room.room_id
        handler._peer_rooms["p1"] = room.room_id

        await handler._handle_disconnect("p1")

        response = json.loads(host_ws.send_text.call_args[0][0])
        assert response["type"] == SignalType.PARTICIPANT_LEFT.value
        assert response["participant_id"] == "p1"

    @pytest.mark.asyncio
    async def test_last_participant_removes_room(self, handler, room_manager):
        register_peer(handler, "host1")
        room = room_manager.create_room("host1")
        handler._peer_rooms["host1"] = room.room_id

        await handler._handle_disconnect("host1")
        assert not room_manager.has_room(room.room_id)

    @pytest.mark.asyncio
    async def test_disconnect_without_room_is_safe(self, handler):
        register_peer(handler, "c1")
        await handler._handle_disconnect("c1")
        assert "c1" not in handler._peers

    @pytest.mark.asyncio
    async def test_disconnect_from_deleted_room(self, handler):
        register_peer(handler, "p1")
        handler._peer_rooms["p1"] = "deleted_room"
        await handler._handle_disconnect("p1")
        assert "p1" not in handler._peer_rooms

    @pytest.mark.asyncio
    async def test_room_not_removed_while_others_remain(self, handler, room_manager):
        register_peer(handler, "host1")
        register_peer(handler, "p1")

        room = room_manager.create_room("host1")
        room_manager.add_participant(room.room_id, "p1")
        handler._peer_rooms["host1"] = room.room_id
        handler._peer_rooms["p1"] = room.room_id

        await handler._handle_disconnect("p1")
        assert room_manager.has_room(room.room_id)


class TestLockUnlock:
    @pytest.mark.asyncio
    async def test_host_can_lock_room(self, handler, room_manager):
        host_ws = register_peer(handler, "host1")
        p1_ws = register_peer(handler, "p1")

        room = room_manager.create_room("host1")
        room_manager.add_participant(room.room_id, "p1")
        handler._peer_rooms["host1"] = room.room_id
        handler._peer_rooms["p1"] = room.room_id

        await handler._handle_message(
            "host1",
            json.dumps({"type": SignalType.LOCK_ROOM.value}),
        )

        host_response = json.loads(host_ws.send_text.call_args[0][0])
        assert host_response["type"] == SignalType.ROOM_LOCKED.value

        p1_response = json.loads(p1_ws.send_text.call_args[0][0])
        assert p1_response["type"] == SignalType.ROOM_LOCKED.value

        assert room.locked is True

    @pytest.mark.asyncio
    async def test_host_can_unlock_room(self, handler, room_manager):
        host_ws = register_peer(handler, "host1")

        room = room_manager.create_room("host1")
        handler._peer_rooms["host1"] = room.room_id
        room_manager.set_room_locked(room.room_id, "host1", locked=True)

        await handler._handle_message(
            "host1",
            json.dumps({"type": SignalType.UNLOCK_ROOM.value}),
        )

        response = json.loads(host_ws.send_text.call_args[0][0])
        assert response["type"] == SignalType.ROOM_UNLOCKED.value
        assert room.locked is False

    @pytest.mark.asyncio
    async def test_lock_without_room_is_safe(self, handler):
        register_peer(handler, "c1")
        await handler._handle_message(
            "c1",
            json.dumps({"type": SignalType.LOCK_ROOM.value}),
        )

    @pytest.mark.asyncio
    async def test_unlock_without_room_is_safe(self, handler):
        register_peer(handler, "c1")
        await handler._handle_message(
            "c1",
            json.dumps({"type": SignalType.UNLOCK_ROOM.value}),
        )


async def handle_and_capture_peers(handler, socket):
    peers_while_connected = {}
    receive = socket.receive

    async def receive_after_capturing():
        peers_while_connected.update(handler._peers)
        return await receive()

    socket.receive = receive_after_capturing
    await handler.handle_websocket(socket)
    return peers_while_connected


class TestHandleWebsocket:
    @pytest.mark.asyncio
    async def test_abnormal_disconnect_is_logged_and_cleans_up(self, handler, room_manager):
        socket = FakeSocket([text_frame({"type": SignalType.JOIN.value}), disconnect_frame(1006)])
        with structlog.testing.capture_logs() as logs:
            await handler.handle_websocket(socket)

        [error_log] = [log for log in logs if log["event"] == "websocket_error"]
        assert error_log["code"] == 1006
        assert room_manager.room_count == 0
        assert handler._peers == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", [1000, 1001, 1005])
    async def test_normal_disconnect_is_not_logged_as_error(self, handler, code):
        socket = FakeSocket([disconnect_frame(code)])
        with structlog.testing.capture_logs() as logs:
            await handler.handle_websocket(socket)

        assert [log for log in logs if log["event"] == "websocket_error"] == []

    @pytest.mark.asyncio
    async def test_binary_frame_is_ignored_and_later_text_frames_still_process(self, handler, room_manager):
        socket = FakeSocket(
            [
                {"type": "websocket.receive", "bytes": b"x"},
                text_frame({"type": SignalType.JOIN.value}),
                disconnect_frame(1005),
            ]
        )
        await handler.handle_websocket(socket)
        assert room_manager.room_count == 0

    @pytest.mark.asyncio
    async def test_refused_handshake_closes_with_policy_violation_without_accepting(self, room_manager):
        refusing_handler = SignalingHandler(room_manager, FixedIdentityResolver(None))
        socket = FakeSocket([])
        await refusing_handler.handle_websocket(socket)
        assert socket.closed_with == 1008
        assert not socket.accepted

    @pytest.mark.asyncio
    async def test_failing_resolver_propagates_without_accepting_the_socket(self, room_manager):
        failing_handler = SignalingHandler(room_manager, RaisingIdentityResolver())
        socket = FakeSocket([])
        with pytest.raises(ResolverError):
            await failing_handler.handle_websocket(socket)
        assert not socket.accepted

    @pytest.mark.asyncio
    async def test_admitted_handshake_accepts_and_registers_a_peer_carrying_the_account(self, handler):
        socket = FakeSocket([disconnect_frame(1005)])
        peers_while_connected = await handle_and_capture_peers(handler, socket)

        assert socket.accepted
        assert [peer.account for peer in peers_while_connected.values()] == [ADMITTED_ACCOUNT]

    @pytest.mark.asyncio
    async def test_two_sockets_of_one_account_become_two_peers(self, handler):
        first = await handle_and_capture_peers(handler, FakeSocket([disconnect_frame(1005)]))
        second = await handle_and_capture_peers(handler, FakeSocket([disconnect_frame(1005)]))

        assert first.keys().isdisjoint(second.keys())


class TestChat:
    @pytest.mark.asyncio
    async def test_chat_broadcasts_to_other_participants(self, handler, room_manager):
        host_ws = register_peer(handler, "host1")
        p1_ws = register_peer(handler, "p1")

        room = room_manager.create_room("host1")
        room_manager.add_participant(room.room_id, "p1")
        handler._peer_rooms["host1"] = room.room_id
        handler._peer_rooms["p1"] = room.room_id

        await handler._handle_message(
            "host1",
            json.dumps({"type": SignalType.CHAT.value, "message": "hello"}),
        )

        p1_response = json.loads(p1_ws.send_text.call_args[0][0])
        assert p1_response["type"] == SignalType.CHAT.value
        assert p1_response["message"] == "hello"
        assert p1_response["from"] == "host1"
        host_ws.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_chat_without_room_is_safe(self, handler):
        register_peer(handler, "c1")
        await handler._handle_message(
            "c1",
            json.dumps({"type": SignalType.CHAT.value, "message": "hello"}),
        )

    @pytest.mark.asyncio
    async def test_chat_empty_message_is_ignored(self, handler, room_manager):
        register_peer(handler, "host1")
        p1_ws = register_peer(handler, "p1")

        room = room_manager.create_room("host1")
        room_manager.add_participant(room.room_id, "p1")
        handler._peer_rooms["host1"] = room.room_id
        handler._peer_rooms["p1"] = room.room_id

        await handler._handle_message(
            "host1",
            json.dumps({"type": SignalType.CHAT.value, "message": ""}),
        )

        p1_ws.send_text.assert_not_called()


class TestSend:
    @pytest.mark.asyncio
    async def test_send_to_connected_client(self, handler):
        ws = register_peer(handler, "c1")
        await handler._send("c1", {"type": "test"})
        ws.send_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_to_unknown_client_is_safe(self, handler):
        await handler._send("unknown", {"type": "test"})

    @pytest.mark.asyncio
    async def test_send_to_closed_ws_is_safe(self, handler):
        ws = register_peer(handler, "c1")
        ws.client_state = WebSocketState.DISCONNECTED
        await handler._send("c1", {"type": "test"})
        ws.send_text.assert_not_called()


class TestSendToGonePeer:
    @pytest.mark.asyncio
    async def test_send_to_peer_whose_application_state_is_disconnected_sends_nothing(self, handler):
        ws = register_peer(handler, "c1")
        ws.application_state = WebSocketState.DISCONNECTED
        await handler._send("c1", {"type": "test"})
        ws.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_relay_to_peer_whose_send_raises_disconnect_keeps_the_sender_running(self, handler):
        sender_ws = register_peer(handler, "p1")
        gone_ws = register_peer(handler, "p2")
        gone_ws.send_text.side_effect = WebSocketDisconnect(code=1006)

        await handler._handle_message("p1", json.dumps({"type": SignalType.OFFER.value, "target": "p2"}))
        await handler._handle_message("p1", "not json")

        response = json.loads(sender_ws.send_text.call_args[0][0])
        assert response["reason"] == ErrorReason.INVALID_MESSAGE.value

    @pytest.mark.asyncio
    async def test_relay_to_missing_targets_through_handle_websocket_sends_the_sender_no_frame(self, handler):
        socket = FakeSocket(
            [
                text_frame({"type": SignalType.OFFER.value, "target": ""}),
                text_frame({"type": SignalType.OFFER.value, "target": "nobody"}),
                disconnect_frame(1005),
            ]
        )
        await handler.handle_websocket(socket)
        assert socket.sent == []

    @pytest.mark.asyncio
    async def test_relay_through_handle_websocket_to_vanished_peer_still_answers_later_frames(self, handler):
        gone_ws = register_peer(handler, "gone")
        gone_ws.send_text.side_effect = WebSocketDisconnect(code=1006)
        socket = FakeSocket(
            [
                text_frame({"type": SignalType.OFFER.value, "target": "gone"}),
                {"type": "websocket.receive", "text": "not json"},
                disconnect_frame(1005),
            ]
        )
        await handler.handle_websocket(socket)
        assert [reply["reason"] for reply in socket.sent] == [ErrorReason.INVALID_MESSAGE.value]

    @pytest.mark.asyncio
    async def test_send_over_a_transport_that_fails_is_a_no_op_via_starlette_translation(self, handler):
        async def receive():
            return {"type": "websocket.connect"}

        async def failing_send(message):
            if message["type"] == "websocket.accept":
                return
            raise OSError("transport closed")

        scope = {"type": "websocket", "path": "/ws", "headers": [], "query_string": b""}
        websocket = WebSocket(scope, receive, failing_send)
        await websocket.accept()
        handler._peers["p1"] = Peer(account=ADMITTED_ACCOUNT, websocket=websocket)

        with structlog.testing.capture_logs() as logs:
            await handler._send("p1", {"type": "test"})

        assert [log["event"] for log in logs] == ["send_to_gone_peer"]
