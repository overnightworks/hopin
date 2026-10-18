import json
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest
import structlog
import structlog.testing
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from hopin.config.constants import WEBSOCKET_DISCONNECT_EVENT, ErrorReason, RoomClosedReason, SignalType
from hopin.rooms.manager import RoomManager
from hopin.rooms.models import Account
from hopin.signaling.handler import Peer, SignalingHandler

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


class ManualScheduler:
    """Runs timers only when the test advances its clock; cancel leaves a started callback alone."""

    def __init__(self):
        self._now = 0.0
        self._timers = []

    def now(self):
        return self._now

    def call_later(self, delay_seconds, callback):
        timer = ManualTimer(self._now + delay_seconds, callback)
        self._timers.append(timer)
        return timer

    async def advance(self, seconds):
        target = self._now + seconds
        while due := [timer for timer in self._timers if timer.pending and timer.deadline <= target]:
            timer = min(due, key=lambda candidate: candidate.deadline)
            self._now = max(self._now, timer.deadline)
            timer.pending = False
            await timer.callback()
        self._now = target


class ManualTimer:
    def __init__(self, deadline, callback):
        self.deadline = deadline
        self.callback = callback
        self.pending = True

    def cancel(self):
        self.pending = False


@pytest.fixture
def scheduler():
    return ManualScheduler()


@pytest.fixture
def handler(room_manager, scheduler):
    return SignalingHandler(room_manager, FixedIdentityResolver(ADMITTED_ACCOUNT), scheduler)


def make_ws_mock():
    ws = AsyncMock()
    ws.client_state = WebSocketState.CONNECTED
    ws.application_state = WebSocketState.CONNECTED
    ws.send_text = AsyncMock()
    return ws


def account_named(name):
    return Account(id=name, name=name)


def register_peer(handler, peer_id, account_id=None):
    ws = make_ws_mock()
    handler._peers[peer_id] = Peer(account=account_named(account_id or peer_id), websocket=ws)
    return ws


def seat_in_room(handler, room_manager, host, *guests):
    """Register the host and guests as connected peers sharing one room, as joins would leave them."""
    sockets = {name: register_peer(handler, name) for name in (host, *guests)}
    room = room_manager.create_room(host, account_named(host))
    handler._peer_rooms[host] = room.room_id
    for guest in guests:
        room_manager.add_participant(room.room_id, guest, account_named(guest))
        handler._peer_rooms[guest] = room.room_id
    return room, sockets


def sent_frames(ws):
    return [json.loads(call.args[0]) for call in ws.send_text.call_args_list]


def sent_types(ws):
    return [frame["type"] for frame in sent_frames(ws)]


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
    room = room_manager.create_room("host1", account_named("host1"))
    room_manager.set_room_locked(room.room_id, account_named("host1"), locked=True)
    return json.dumps({"type": SignalType.JOIN.value, "room_id": room.room_id})


def join_full_room(_handler, room_manager):
    room = room_manager.create_room("host1", account_named("host1"))
    for index in range(ROOM_LIMIT - 1):
        room_manager.add_participant(room.room_id, f"member{index}", account_named(f"member{index}"))
    return json.dumps({"type": SignalType.JOIN.value, "room_id": room.room_id})


def lock_as_participant(handler, room_manager):
    room = room_manager.create_room("host1", account_named("host1"))
    room_manager.add_participant(room.room_id, "sender", account_named("sender"))
    handler._peer_rooms["sender"] = room.room_id
    return json.dumps({"type": SignalType.LOCK_ROOM.value})


def end_room_as_participant(handler, room_manager):
    lock_as_participant(handler, room_manager)
    return json.dumps({"type": SignalType.END_ROOM.value})


def lock_while_host_is_away(handler, room_manager):
    request = lock_as_participant(handler, room_manager)
    room_id = handler._peer_rooms["sender"]
    room_manager.remove_participant(room_id, "host1")
    return request


class TestHandleMessageRefusals:
    @pytest.mark.parametrize(
        ("reason", "arrange"),
        [
            pytest.param(ErrorReason.NOT_FOUND, join_unknown_room, id="join_unknown_room"),
            pytest.param(ErrorReason.LOCKED, join_locked_room, id="join_locked_room"),
            pytest.param(ErrorReason.FULL, join_full_room, id="join_full_room"),
            pytest.param(ErrorReason.NOT_HOST, lock_as_participant, id="lock_as_participant"),
            pytest.param(ErrorReason.NOT_HOST, end_room_as_participant, id="end_room_as_participant"),
            pytest.param(ErrorReason.NOT_HOST, lock_while_host_is_away, id="lock_while_host_is_away"),
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

        room = room_manager.create_room("host1", account_named("host1"))
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

        room = room_manager.create_room("host1", account_named("host1"))
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
        room = manager.create_room("host1", account_named("host1"))
        register_peer(handler, "host1")
        handler._peer_rooms["host1"] = room.room_id
        manager.add_participant(room.room_id, "p1", account_named("p1"))

        ws = register_peer(handler, "p2")
        await handler._handle_message(
            "p2",
            json.dumps({"type": SignalType.JOIN.value, "room_id": room.room_id}),
        )
        response = json.loads(ws.send_text.call_args[0][0])
        assert response["type"] == SignalType.ERROR.value


class TestRelay:
    @pytest.mark.asyncio
    async def test_offer_relayed_to_target(self, handler, room_manager):
        _room, sockets = seat_in_room(handler, room_manager, "p1", "p2")
        target_ws = sockets["p2"]

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
    async def test_answer_relayed_to_target(self, handler, room_manager):
        _room, sockets = seat_in_room(handler, room_manager, "p1", "p2")
        target_ws = sockets["p1"]

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
    async def test_ice_candidate_relayed_to_target(self, handler, room_manager):
        _room, sockets = seat_in_room(handler, room_manager, "p1", "p2")
        target_ws = sockets["p2"]

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

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "signal_type",
        [SignalType.OFFER, SignalType.ANSWER, SignalType.ICE_CANDIDATE],
    )
    async def test_relay_to_a_peer_in_another_room_is_a_no_op(self, handler, room_manager, signal_type):
        _room, sockets = seat_in_room(handler, room_manager, "p1", "p2")
        _other_room, other_sockets = seat_in_room(handler, room_manager, "outsider")

        await handler._handle_message("p1", json.dumps({"type": signal_type.value, "target": "outsider"}))

        assert sent_frames(sockets["p1"]) == []
        other_sockets["outsider"].send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_relay_from_a_sender_in_no_room_is_a_no_op(self, handler, room_manager):
        sender_ws = register_peer(handler, "loner")
        _room, sockets = seat_in_room(handler, room_manager, "p1")

        await handler._handle_message("loner", json.dumps({"type": SignalType.OFFER.value, "target": "p1"}))

        sockets["p1"].send_text.assert_not_called()
        sender_ws.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_relay_to_a_peer_in_no_room_is_a_no_op(self, handler, room_manager):
        seat_in_room(handler, room_manager, "p1")
        loner_ws = register_peer(handler, "loner")

        await handler._handle_message("p1", json.dumps({"type": SignalType.OFFER.value, "target": "loner"}))

        loner_ws.send_text.assert_not_called()


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_participant_disconnect_notifies_others(self, handler, room_manager):
        host_ws = register_peer(handler, "host1")
        register_peer(handler, "p1")

        room = room_manager.create_room("host1", account_named("host1"))
        room_manager.add_participant(room.room_id, "p1", account_named("p1"))
        handler._peer_rooms["host1"] = room.room_id
        handler._peer_rooms["p1"] = room.room_id

        await handler._handle_disconnect("p1")

        response = json.loads(host_ws.send_text.call_args[0][0])
        assert response["type"] == SignalType.PARTICIPANT_LEFT.value
        assert response["participant_id"] == "p1"

    @pytest.mark.asyncio
    async def test_last_guest_leaving_keeps_the_room(self, handler, room_manager):
        room, _sockets = seat_in_room(handler, room_manager, "host1", "p1")
        await handler._handle_disconnect("p1")
        await handler._handle_disconnect("host1")
        assert room_manager.has_room(room.room_id)

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

        room = room_manager.create_room("host1", account_named("host1"))
        room_manager.add_participant(room.room_id, "p1", account_named("p1"))
        handler._peer_rooms["host1"] = room.room_id
        handler._peer_rooms["p1"] = room.room_id

        await handler._handle_disconnect("p1")
        assert room_manager.has_room(room.room_id)


class TestLockUnlock:
    @pytest.mark.asyncio
    async def test_host_can_lock_room(self, handler, room_manager):
        host_ws = register_peer(handler, "host1")
        p1_ws = register_peer(handler, "p1")

        room = room_manager.create_room("host1", account_named("host1"))
        room_manager.add_participant(room.room_id, "p1", account_named("p1"))
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

        room = room_manager.create_room("host1", account_named("host1"))
        handler._peer_rooms["host1"] = room.room_id
        room_manager.set_room_locked(room.room_id, account_named("host1"), locked=True)

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
        assert room_manager.room_count == 1
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
        assert room_manager.room_count == 1

    @pytest.mark.asyncio
    async def test_refused_handshake_closes_with_policy_violation_without_accepting(self, room_manager):
        refusing_handler = SignalingHandler(room_manager, FixedIdentityResolver(None), ManualScheduler())
        socket = FakeSocket([])
        await refusing_handler.handle_websocket(socket)
        assert socket.closed_with == 1008
        assert not socket.accepted

    @pytest.mark.asyncio
    async def test_failing_resolver_propagates_without_accepting_the_socket(self, room_manager):
        failing_handler = SignalingHandler(room_manager, RaisingIdentityResolver(), ManualScheduler())
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

        room = room_manager.create_room("host1", account_named("host1"))
        room_manager.add_participant(room.room_id, "p1", account_named("p1"))
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

        room = room_manager.create_room("host1", account_named("host1"))
        room_manager.add_participant(room.room_id, "p1", account_named("p1"))
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
    async def test_relay_to_peer_whose_send_raises_disconnect_keeps_the_sender_running(self, handler, room_manager):
        _room, sockets = seat_in_room(handler, room_manager, "p1", "p2")
        sender_ws, gone_ws = sockets["p1"], sockets["p2"]
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
    async def test_relay_through_handle_websocket_to_vanished_peer_still_answers_later_frames(
        self, handler, room_manager
    ):
        gone_ws = register_peer(handler, "gone")
        gone_ws.send_text.side_effect = WebSocketDisconnect(code=1006)
        room = room_manager.create_room("gone", account_named("gone"))
        handler._peer_rooms["gone"] = room.room_id
        socket = FakeSocket(
            [
                text_frame({"type": SignalType.JOIN.value, "room_id": room.room_id}),
                text_frame({"type": SignalType.OFFER.value, "target": "gone"}),
                {"type": "websocket.receive", "text": "not json"},
                disconnect_frame(1005),
            ]
        )
        await handler.handle_websocket(socket)
        errors = [reply["reason"] for reply in socket.sent if reply["type"] == SignalType.ERROR.value]
        assert errors == [ErrorReason.INVALID_MESSAGE.value]

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


def join_message(room_id=None):
    return json.dumps({"type": SignalType.JOIN.value, "room_id": room_id})


def frames_of_type(ws, signal_type):
    return [frame for frame in sent_frames(ws) if frame["type"] == signal_type.value]


async def drop_host(handler, room_manager, *guests):
    room, sockets = seat_in_room(handler, room_manager, "host1", *guests)
    await handler._handle_disconnect("host1")
    return room, sockets


async def host_rejoins(handler, room_id, peer_id="host-back"):
    ws = register_peer(handler, peer_id, account_id="host1")
    await handler._handle_message(peer_id, join_message(room_id))
    return ws


class TestHostGrace:
    @pytest.mark.asyncio
    async def test_last_host_connection_dropping_sends_host_away_then_participant_left_and_keeps_the_room(
        self, handler, room_manager
    ):
        room, sockets = await drop_host(handler, room_manager, "p1")

        assert sent_frames(sockets["p1"]) == [
            {"type": SignalType.HOST_AWAY.value, "seconds_left": 60, "participant_id": "host1"},
            {"type": SignalType.PARTICIPANT_LEFT.value, "participant_id": "host1"},
        ]
        assert room_manager.has_room(room.room_id)

    @pytest.mark.asyncio
    async def test_guest_leaving_sends_no_host_away(self, handler, room_manager):
        _room, sockets = seat_in_room(handler, room_manager, "host1", "p1")

        await handler._handle_disconnect("p1")

        assert sent_types(sockets["host1"]) == [SignalType.PARTICIPANT_LEFT.value]

    @pytest.mark.asyncio
    async def test_room_survives_until_the_grace_runs_out_then_closes_for_every_peer_as_host_gone(
        self, handler, room_manager, scheduler
    ):
        room, sockets = await drop_host(handler, room_manager, "p1", "p2")

        await scheduler.advance(59)
        assert room_manager.has_room(room.room_id)
        await scheduler.advance(1)

        closed = {"type": SignalType.ROOM_CLOSED.value, "reason": RoomClosedReason.HOST_GONE.value}
        assert frames_of_type(sockets["p1"], SignalType.ROOM_CLOSED) == [closed]
        assert frames_of_type(sockets["p2"], SignalType.ROOM_CLOSED) == [closed]
        assert not room_manager.has_room(room.room_id)

    @pytest.mark.asyncio
    async def test_former_member_of_a_closed_room_cannot_lock_or_chat(self, handler, room_manager, scheduler):
        _room, sockets = await drop_host(handler, room_manager, "p1", "p2")
        await scheduler.advance(60)
        frames_before = {name: len(sent_frames(ws)) for name, ws in sockets.items()}

        await handler._handle_message("p1", json.dumps({"type": SignalType.LOCK_ROOM.value}))
        await handler._handle_message("p1", json.dumps({"type": SignalType.CHAT.value, "message": "hi"}))

        assert {name: len(sent_frames(ws)) for name, ws in sockets.items()} == frames_before

    @pytest.mark.asyncio
    async def test_room_of_a_host_who_was_alone_closes_when_the_grace_runs_out(self, handler, room_manager, scheduler):
        room, _sockets = await drop_host(handler, room_manager)

        assert room_manager.has_room(room.room_id)
        await scheduler.advance(60)

        assert not room_manager.has_room(room.room_id)

    @pytest.mark.asyncio
    async def test_host_account_rejoining_within_the_grace_is_host_again_and_the_room_stays(
        self, handler, room_manager, scheduler
    ):
        room, sockets = await drop_host(handler, room_manager, "p1")
        await scheduler.advance(30)

        returning_ws = await host_rejoins(handler, room.room_id)
        await scheduler.advance(60)

        [existing] = frames_of_type(returning_ws, SignalType.EXISTING_PARTICIPANTS)
        assert existing["is_host"] is True
        assert sent_frames(sockets["p1"])[-2:] == [
            {"type": SignalType.PARTICIPANT_JOINED.value, "participant_id": "host-back"},
            {"type": SignalType.HOST_BACK.value, "participant_id": "host-back"},
        ]
        assert frames_of_type(sockets["p1"], SignalType.ROOM_CLOSED) == []
        assert room_manager.has_room(room.room_id)

    @pytest.mark.asyncio
    async def test_host_who_was_alone_finds_the_room_on_reload(self, handler, room_manager, scheduler):
        room, _sockets = await drop_host(handler, room_manager)

        returning_ws = await host_rejoins(handler, room.room_id)
        await scheduler.advance(60)

        assert frames_of_type(returning_ws, SignalType.EXISTING_PARTICIPANTS)[0]["is_host"] is True
        assert room_manager.has_room(room.room_id)

    @pytest.mark.asyncio
    async def test_host_account_is_admitted_past_the_lock_during_the_grace(self, handler, room_manager):
        room, sockets = await drop_host(handler, room_manager, "p1")
        room.locked = True

        returning_ws = await host_rejoins(handler, room.room_id)

        assert frames_of_type(returning_ws, SignalType.EXISTING_PARTICIPANTS)[0]["locked"] is True
        assert frames_of_type(sockets["p1"], SignalType.HOST_BACK)

    @pytest.mark.asyncio
    async def test_host_account_returns_into_its_reserved_seat_of_an_otherwise_full_room(self, handler, room_manager):
        guests = [f"g{index}" for index in range(ROOM_LIMIT - 1)]
        room, _sockets = await drop_host(handler, room_manager, *guests)
        stranger_ws = register_peer(handler, "stranger")
        await handler._handle_message("stranger", join_message(room.room_id))

        returning_ws = await host_rejoins(handler, room.room_id)

        assert frames_of_type(stranger_ws, SignalType.ERROR)[0]["reason"] == ErrorReason.FULL.value
        assert frames_of_type(returning_ws, SignalType.EXISTING_PARTICIPANTS)

    @pytest.mark.asyncio
    async def test_joiner_during_the_grace_gets_host_away_with_the_seconds_left_after_the_existing_list(
        self, handler, room_manager, scheduler
    ):
        room, sockets = await drop_host(handler, room_manager, "p1")
        await scheduler.advance(20)
        joiner_ws = register_peer(handler, "p2")

        await handler._handle_message("p2", join_message(room.room_id))

        assert sent_types(joiner_ws) == [SignalType.EXISTING_PARTICIPANTS.value, SignalType.HOST_AWAY.value]
        assert frames_of_type(joiner_ws, SignalType.HOST_AWAY)[0]["seconds_left"] == 40
        assert frames_of_type(sockets["p1"], SignalType.HOST_BACK) == []

    @pytest.mark.asyncio
    async def test_host_with_two_connections_keeps_the_room_hosted_when_one_drops(self, handler, room_manager):
        room, sockets = seat_in_room(handler, room_manager, "host1", "p1")
        second_ws = await host_rejoins(handler, room.room_id, "host-second")

        await handler._handle_disconnect("host1")
        await handler._handle_message("host-second", json.dumps({"type": SignalType.LOCK_ROOM.value}))

        assert frames_of_type(sockets["p1"], SignalType.HOST_AWAY) == []
        assert frames_of_type(second_ws, SignalType.ROOM_LOCKED)
        assert frames_of_type(second_ws, SignalType.EXISTING_PARTICIPANTS)[0]["is_host"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("signal_type", [SignalType.LOCK_ROOM, SignalType.UNLOCK_ROOM])
    async def test_lock_and_unlock_during_the_grace_answer_not_host_and_leave_the_lock_alone(
        self, handler, room_manager, signal_type
    ):
        room, sockets = await drop_host(handler, room_manager, "p1")

        await handler._handle_message("p1", json.dumps({"type": signal_type.value}))

        assert frames_of_type(sockets["p1"], SignalType.ERROR)[0]["reason"] == ErrorReason.NOT_HOST.value
        assert room.locked is False


class TestEndRoom:
    @pytest.mark.asyncio
    async def test_host_ending_the_room_closes_it_as_ended_for_every_peer_including_the_host(
        self, handler, room_manager
    ):
        room, sockets = seat_in_room(handler, room_manager, "host1", "p1")

        await handler._handle_message("host1", json.dumps({"type": SignalType.END_ROOM.value}))

        closed = {"type": SignalType.ROOM_CLOSED.value, "reason": RoomClosedReason.ENDED.value}
        assert frames_of_type(sockets["host1"], SignalType.ROOM_CLOSED) == [closed]
        assert frames_of_type(sockets["p1"], SignalType.ROOM_CLOSED) == [closed]
        assert not room_manager.has_room(room.room_id)

    @pytest.mark.asyncio
    async def test_end_room_from_a_peer_in_no_room_is_ignored(self, handler):
        ws = register_peer(handler, "c1")

        await handler._handle_message("c1", json.dumps({"type": SignalType.END_ROOM.value}))

        ws.send_text.assert_not_called()


class TestExpiryRace:
    @pytest.mark.asyncio
    async def test_expiry_after_the_host_returned_does_nothing(self, handler, room_manager):
        room, sockets = await drop_host(handler, room_manager, "p1")
        await host_rejoins(handler, room.room_id)

        await handler._expire_grace(room.room_id)

        assert frames_of_type(sockets["p1"], SignalType.ROOM_CLOSED) == []
        assert room_manager.has_room(room.room_id)
