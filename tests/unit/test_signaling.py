import asyncio
import json
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
import structlog
import structlog.testing
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from hopin.config.constants import (
    HOST_GRACE_SECONDS,
    MAX_SIGNALING_FRAME_LENGTH,
    REPLAY_LOG_LIMIT,
    RESUME_WINDOW_SECONDS,
    SUPERSEDED_CLOSE_CODE,
    WEBSOCKET_DISCONNECT_EVENT,
    ErrorReason,
    RoomClosedReason,
    SignalType,
)
from hopin.rooms.manager import RoomManager
from hopin.rooms.models import Account
from hopin.signaling.handler import Connection, Peer, SignalingHandler

ROOM_LIMIT = 5


@pytest.fixture
def room_manager():
    return RoomManager(max_participants_per_room=ROOM_LIMIT)


class SocketAccountResolver:
    async def resolve(self, websocket):
        return websocket.account


class ResolverError(Exception):
    pass


class RaisingIdentityResolver:
    async def resolve(self, _websocket):
        raise ResolverError


ADMITTED_ACCOUNT = Account(id="c1", name="c1")
SETTLE_TIMEOUT_SECONDS = 5


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
    return SignalingHandler(room_manager, SocketAccountResolver(), scheduler)


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


async def deliver(handler, peer_id, message):
    """Hand the handler the next frame of the peer's current socket, numbered as a browser would."""
    peer = handler._peers[peer_id]
    raw = message if isinstance(message, str) else json.dumps({**message, "seq": peer.received_seq + 1})
    await handler._handle_message(Connection(peer.websocket, peer_id, frames_received=1), raw)


def numbered_frames(ws):
    return [json.loads(call.args[0]) for call in ws.send_text.call_args_list]


def sent_frames(ws):
    """The frames the socket was sent, without the sequence numbers a test about content does not care for."""
    return [{key: value for key, value in frame.items() if key != "seq"} for frame in numbered_frames(ws)]


def sent_types(ws):
    return [frame["type"] for frame in sent_frames(ws)]


def text_frame(data, seq=None):
    numbered = data if seq is None else {**data, "seq": seq}
    return {"type": "websocket.receive", "text": json.dumps(numbered)}


def disconnect_frame(code):
    return {"type": WEBSOCKET_DISCONNECT_EVENT, "code": code}


@dataclass(eq=False)
class FakeSocket:
    """A minimal socket: the test pushes inbound frames and reads what the handler sent."""

    frames: list[dict]
    account: Account | None = ADMITTED_ACCOUNT
    client_state: WebSocketState = WebSocketState.CONNECTED
    application_state: WebSocketState = WebSocketState.CONNECTED
    accepted: bool = False
    closed_with: int | None = None
    sent: list[dict] = field(default_factory=list)
    send_gate: asyncio.Event | None = None
    idle: asyncio.Event = field(default_factory=asyncio.Event)
    inbox: asyncio.Queue = field(default_factory=asyncio.Queue)

    def __post_init__(self) -> None:
        for frame in self.frames:
            self.inbox.put_nowait(frame)

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int) -> None:
        self.closed_with = code
        self.application_state = WebSocketState.DISCONNECTED

    async def receive(self) -> dict:
        if self.inbox.empty():
            self.idle.set()
        frame = await self.inbox.get()
        self.idle.clear()
        if frame["type"] == WEBSOCKET_DISCONNECT_EVENT:
            self.client_state = WebSocketState.DISCONNECTED
        return frame

    def push(self, frame: dict) -> None:
        self.idle.clear()
        self.inbox.put_nowait(frame)

    async def send_text(self, text: str) -> None:
        if self.send_gate is not None:
            await self.send_gate.wait()
        self.sent.append(json.loads(text))


def join_unknown_room(_handler, _room_manager):
    return {"type": SignalType.JOIN.value, "room_id": "nonexistent"}


def join_locked_room(_handler, room_manager):
    room = room_manager.create_room("host1", account_named("host1"))
    room_manager.set_room_locked(room.room_id, account_named("host1"), locked=True)
    return {"type": SignalType.JOIN.value, "room_id": room.room_id}


def join_full_room(_handler, room_manager):
    room = room_manager.create_room("host1", account_named("host1"))
    for index in range(ROOM_LIMIT - 1):
        room_manager.add_participant(room.room_id, f"member{index}", account_named(f"member{index}"))
    return {"type": SignalType.JOIN.value, "room_id": room.room_id}


def lock_as_participant(handler, room_manager):
    room = room_manager.create_room("host1", account_named("host1"))
    room_manager.add_participant(room.room_id, "sender", account_named("sender"))
    handler._peer_rooms["sender"] = room.room_id
    return {"type": SignalType.LOCK_ROOM.value}


def end_room_as_participant(handler, room_manager):
    lock_as_participant(handler, room_manager)
    return {"type": SignalType.END_ROOM.value}


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
            pytest.param(ErrorReason.INVALID_MESSAGE, lambda *_: {"foo": "bar"}, id="no_type"),
            pytest.param(ErrorReason.INVALID_MESSAGE, lambda *_: {"type": "unknown"}, id="unknown_type"),
            pytest.param(ErrorReason.INVALID_MESSAGE, lambda *_: {"type": []}, id="non_string_type"),
            pytest.param(
                ErrorReason.INVALID_MESSAGE,
                lambda *_: {"type": SignalType.OFFER.value, "target": []},
                id="non_string_target",
            ),
            pytest.param(
                ErrorReason.INVALID_MESSAGE,
                lambda *_: {"type": SignalType.JOIN.value, "room_id": {}},
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

        await deliver(handler, "sender", raw_data)

        ws.send_text.assert_called_once()
        response = json.loads(ws.send_text.call_args[0][0])
        assert response["type"] == SignalType.ERROR.value
        assert response["reason"] == reason.value
        assert response["message"]


class TestJoinCreateRoom:
    @pytest.mark.asyncio
    async def test_creates_room_when_no_room_id(self, handler):
        ws = register_peer(handler, "host1")
        await deliver(
            handler,
            "host1",
            {"type": SignalType.JOIN.value},
        )
        response = json.loads(ws.send_text.call_args[0][0])
        assert response["type"] == SignalType.ROOM_CREATED.value
        assert "room_id" in response

    @pytest.mark.asyncio
    async def test_registers_connection_to_room(self, handler):
        register_peer(handler, "host1")
        await deliver(
            handler,
            "host1",
            {"type": SignalType.JOIN.value},
        )
        assert "host1" in handler._peer_rooms


class TestJoinExistingRoom:
    @pytest.mark.asyncio
    async def test_joins_room_and_gets_existing_participants(self, handler, room_manager):
        register_peer(handler, "host1")
        register_peer(handler, "p1")

        room = room_manager.create_room("host1", account_named("host1"))
        handler._peer_rooms["host1"] = room.room_id

        await deliver(
            handler,
            "p1",
            {"type": SignalType.JOIN.value, "room_id": room.room_id},
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

        await deliver(
            handler,
            "p1",
            {"type": SignalType.JOIN.value, "room_id": room.room_id},
        )

        host_calls = host_ws.send_text.call_args_list
        joined_msg = json.loads(host_calls[0][0][0])
        assert joined_msg["type"] == SignalType.PARTICIPANT_JOINED.value
        assert joined_msg["participant_id"] == "p1"

    @pytest.mark.asyncio
    async def test_invalid_room_sends_error(self, handler):
        ws = register_peer(handler, "p1")
        await deliver(
            handler,
            "p1",
            {"type": SignalType.JOIN.value, "room_id": "nonexistent"},
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
        await deliver(
            handler,
            "p2",
            {"type": SignalType.JOIN.value, "room_id": room.room_id},
        )
        response = json.loads(ws.send_text.call_args[0][0])
        assert response["type"] == SignalType.ERROR.value


class TestRelay:
    @pytest.mark.asyncio
    async def test_offer_relayed_to_target(self, handler, room_manager):
        _room, sockets = seat_in_room(handler, room_manager, "p1", "p2")
        target_ws = sockets["p2"]

        await deliver(
            handler,
            "p1",
            {
                "type": SignalType.OFFER.value,
                "target": "p2",
                "sdp": "test_sdp",
            },
        )

        response = json.loads(target_ws.send_text.call_args[0][0])
        assert response["type"] == SignalType.OFFER.value
        assert response["sdp"] == "test_sdp"
        assert response["from"] == "p1"

    @pytest.mark.asyncio
    async def test_answer_relayed_to_target(self, handler, room_manager):
        _room, sockets = seat_in_room(handler, room_manager, "p1", "p2")
        target_ws = sockets["p1"]

        await deliver(
            handler,
            "p2",
            {
                "type": SignalType.ANSWER.value,
                "target": "p1",
                "sdp": "answer_sdp",
            },
        )

        response = json.loads(target_ws.send_text.call_args[0][0])
        assert response["type"] == SignalType.ANSWER.value
        assert response["sdp"] == "answer_sdp"
        assert response["from"] == "p2"

    @pytest.mark.asyncio
    async def test_ice_candidate_relayed_to_target(self, handler, room_manager):
        _room, sockets = seat_in_room(handler, room_manager, "p1", "p2")
        target_ws = sockets["p2"]

        await deliver(
            handler,
            "p1",
            {
                "type": SignalType.ICE_CANDIDATE.value,
                "target": "p2",
                "candidate": "test_candidate",
            },
        )

        response = json.loads(target_ws.send_text.call_args[0][0])
        assert response["type"] == SignalType.ICE_CANDIDATE.value
        assert response["candidate"] == "test_candidate"
        assert response["from"] == "p1"

    @pytest.mark.asyncio
    async def test_relay_without_target_is_ignored(self, handler):
        ws = register_peer(handler, "p1")
        await deliver(
            handler,
            "p1",
            {"type": SignalType.OFFER.value, "sdp": "test_sdp"},
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

        await deliver(handler, "p1", {"type": signal_type.value, "target": "outsider"})

        assert sent_frames(sockets["p1"]) == []
        other_sockets["outsider"].send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_relay_from_a_sender_in_no_room_is_a_no_op(self, handler, room_manager):
        sender_ws = register_peer(handler, "loner")
        _room, sockets = seat_in_room(handler, room_manager, "p1")

        await deliver(handler, "loner", {"type": SignalType.OFFER.value, "target": "p1"})

        sockets["p1"].send_text.assert_not_called()
        sender_ws.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_relay_to_a_peer_in_no_room_is_a_no_op(self, handler, room_manager):
        seat_in_room(handler, room_manager, "p1")
        loner_ws = register_peer(handler, "loner")

        await deliver(handler, "p1", {"type": SignalType.OFFER.value, "target": "loner"})

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

        await deliver(
            handler,
            "host1",
            {"type": SignalType.LOCK_ROOM.value},
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

        await deliver(
            handler,
            "host1",
            {"type": SignalType.UNLOCK_ROOM.value},
        )

        response = json.loads(host_ws.send_text.call_args[0][0])
        assert response["type"] == SignalType.ROOM_UNLOCKED.value
        assert room.locked is False

    @pytest.mark.asyncio
    async def test_lock_without_room_is_safe(self, handler):
        register_peer(handler, "c1")
        await deliver(
            handler,
            "c1",
            {"type": SignalType.LOCK_ROOM.value},
        )

    @pytest.mark.asyncio
    async def test_unlock_without_room_is_safe(self, handler):
        register_peer(handler, "c1")
        await deliver(
            handler,
            "c1",
            {"type": SignalType.UNLOCK_ROOM.value},
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
    async def test_abnormal_disconnect_is_logged(self, handler, room_manager):
        socket = FakeSocket([text_frame({"type": SignalType.JOIN.value}), disconnect_frame(1006)])
        with structlog.testing.capture_logs() as logs:
            await handler.handle_websocket(socket)

        [error_log] = [log for log in logs if log["event"] == "websocket_error"]
        assert error_log["code"] == 1006
        assert room_manager.room_count == 1

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
        refusing_handler = SignalingHandler(room_manager, SocketAccountResolver(), ManualScheduler())
        socket = FakeSocket([], account=None)
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

        await deliver(
            handler,
            "host1",
            {"type": SignalType.CHAT.value, "message": "hello"},
        )

        p1_response = json.loads(p1_ws.send_text.call_args[0][0])
        assert p1_response["type"] == SignalType.CHAT.value
        assert p1_response["message"] == "hello"
        assert p1_response["from"] == "host1"
        host_ws.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_chat_without_room_is_safe(self, handler):
        register_peer(handler, "c1")
        await deliver(
            handler,
            "c1",
            {"type": SignalType.CHAT.value, "message": "hello"},
        )

    @pytest.mark.asyncio
    async def test_chat_empty_message_is_ignored(self, handler, room_manager):
        register_peer(handler, "host1")
        p1_ws = register_peer(handler, "p1")

        room = room_manager.create_room("host1", account_named("host1"))
        room_manager.add_participant(room.room_id, "p1", account_named("p1"))
        handler._peer_rooms["host1"] = room.room_id
        handler._peer_rooms["p1"] = room.room_id

        await deliver(
            handler,
            "host1",
            {"type": SignalType.CHAT.value, "message": ""},
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

        await deliver(handler, "p1", {"type": SignalType.OFFER.value, "target": "p2"})
        await deliver(handler, "p1", "not json")

        response = json.loads(sender_ws.send_text.call_args[0][0])
        assert response["reason"] == ErrorReason.INVALID_MESSAGE.value

    @pytest.mark.asyncio
    async def test_relay_to_missing_targets_through_handle_websocket_sends_the_sender_no_frame(self, handler):
        socket = FakeSocket(
            [
                text_frame({"type": SignalType.OFFER.value, "target": ""}, seq=1),
                text_frame({"type": SignalType.OFFER.value, "target": "nobody"}, seq=2),
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
                text_frame({"type": SignalType.OFFER.value, "target": "gone"}, seq=1),
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
    return {"type": SignalType.JOIN.value, "room_id": room_id}


def frames_of_type(ws, signal_type):
    return [frame for frame in sent_frames(ws) if frame["type"] == signal_type.value]


async def drop_host(handler, room_manager, *guests):
    room, sockets = seat_in_room(handler, room_manager, "host1", *guests)
    await handler._handle_disconnect("host1")
    return room, sockets


async def host_rejoins(handler, room_id, peer_id="host-back"):
    ws = register_peer(handler, peer_id, account_id="host1")
    await deliver(handler, peer_id, join_message(room_id))
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

        await deliver(handler, "p1", {"type": SignalType.LOCK_ROOM.value})
        await deliver(handler, "p1", {"type": SignalType.CHAT.value, "message": "hi"})

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
        await deliver(handler, "stranger", join_message(room.room_id))

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

        await deliver(handler, "p2", join_message(room.room_id))

        assert sent_types(joiner_ws) == [SignalType.EXISTING_PARTICIPANTS.value, SignalType.HOST_AWAY.value]
        assert frames_of_type(joiner_ws, SignalType.HOST_AWAY)[0]["seconds_left"] == 40
        assert frames_of_type(sockets["p1"], SignalType.HOST_BACK) == []

    @pytest.mark.asyncio
    async def test_host_with_two_connections_keeps_the_room_hosted_when_one_drops(self, handler, room_manager):
        room, sockets = seat_in_room(handler, room_manager, "host1", "p1")
        second_ws = await host_rejoins(handler, room.room_id, "host-second")

        await handler._handle_disconnect("host1")
        await deliver(handler, "host-second", {"type": SignalType.LOCK_ROOM.value})

        assert frames_of_type(sockets["p1"], SignalType.HOST_AWAY) == []
        assert frames_of_type(second_ws, SignalType.ROOM_LOCKED)
        assert frames_of_type(second_ws, SignalType.EXISTING_PARTICIPANTS)[0]["is_host"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("signal_type", [SignalType.LOCK_ROOM, SignalType.UNLOCK_ROOM])
    async def test_lock_and_unlock_during_the_grace_answer_not_host_and_leave_the_lock_alone(
        self, handler, room_manager, signal_type
    ):
        room, sockets = await drop_host(handler, room_manager, "p1")

        await deliver(handler, "p1", {"type": signal_type.value})

        assert frames_of_type(sockets["p1"], SignalType.ERROR)[0]["reason"] == ErrorReason.NOT_HOST.value
        assert room.locked is False


class TestEndRoom:
    @pytest.mark.asyncio
    async def test_host_ending_the_room_closes_it_as_ended_for_every_peer_including_the_host(
        self, handler, room_manager
    ):
        room, sockets = seat_in_room(handler, room_manager, "host1", "p1")

        await deliver(handler, "host1", {"type": SignalType.END_ROOM.value})

        closed = {"type": SignalType.ROOM_CLOSED.value, "reason": RoomClosedReason.ENDED.value}
        assert frames_of_type(sockets["host1"], SignalType.ROOM_CLOSED) == [closed]
        assert frames_of_type(sockets["p1"], SignalType.ROOM_CLOSED) == [closed]
        assert not room_manager.has_room(room.room_id)

    @pytest.mark.asyncio
    async def test_end_room_from_a_peer_in_no_room_is_ignored(self, handler):
        ws = register_peer(handler, "c1")

        await deliver(handler, "c1", {"type": SignalType.END_ROOM.value})

        ws.send_text.assert_not_called()


class TestExpiryRace:
    @pytest.mark.asyncio
    async def test_expiry_after_the_host_returned_does_nothing(self, handler, room_manager):
        room, sockets = await drop_host(handler, room_manager, "p1")
        await host_rejoins(handler, room.room_id)

        await handler._expire_grace(room.room_id)

        assert frames_of_type(sockets["p1"], SignalType.ROOM_CLOSED) == []
        assert room_manager.has_room(room.room_id)


class Browser:
    """One tab in a call: its sockets, the seat credentials it was handed and the frames it numbers."""

    def __init__(self, link, name):
        self.link = link
        self.name = name
        self.sockets = []
        self.peer_id = None
        self.token = None
        self.sent_count = 0

    @property
    def socket(self):
        return self.sockets[-1]

    @property
    def received_seq(self):
        return max((frame["seq"] for socket in self.sockets for frame in socket.sent if "seq" in frame), default=0)

    def frames(self, *signal_types):
        wanted = {signal_type.value for signal_type in signal_types}
        return [frame for socket in self.sockets for frame in socket.sent if frame["type"] in wanted]

    def take_credentials(self, frame):
        self.peer_id, self.token = frame["participant_id"], frame["resume_token"]

    async def say(self, data):
        self.sent_count += 1
        await self.send_raw({**data, "seq": self.sent_count})

    async def send_raw(self, data):
        self.socket.push(text_frame(data))
        await self.link.settle(self.socket)

    async def drop(self, code=1006):
        self.socket.push(disconnect_frame(code))
        await self.link.tasks[self.socket]

    def resume_frame(self, **overrides):
        return {
            "type": SignalType.RESUME.value,
            "participant_id": self.peer_id,
            "token": self.token,
            "last_seq": self.received_seq,
            "sent_floor": 0,
            **overrides,
        }

    async def resume(self, account=None, **overrides):
        self.sockets.append(await self.link.open(account or account_named(self.name)))
        await self.send_raw(self.resume_frame(**overrides))
        return self.socket

    async def accept_resumption(self):
        [resumed] = self.frames(SignalType.RESUMED)[-1:]
        self.token = resumed["resume_token"]


class Link:
    """Runs sockets through the handler as tasks, as the web server would."""

    def __init__(self, handler):
        self.handler = handler
        self.tasks = {}

    async def open(self, account, **socket_options):
        socket = FakeSocket([], account=account, **socket_options)
        self.tasks[socket] = asyncio.create_task(self.handler.handle_websocket(socket))
        await self.settle(socket)
        return socket

    async def settle(self, socket):
        task = self.tasks[socket]
        idle = asyncio.ensure_future(socket.idle.wait())
        done, _ = await asyncio.wait({idle, task}, timeout=SETTLE_TIMEOUT_SECONDS, return_when=asyncio.FIRST_COMPLETED)
        idle.cancel()
        if task in done:
            task.result()
        else:
            assert idle in done, "the handler neither went idle nor finished"

    async def join(self, name, room_id=None):
        browser = Browser(self, name)
        browser.sockets.append(await self.open(account_named(name)))
        await browser.send_raw({"type": SignalType.JOIN.value, "room_id": room_id})
        [first_reply] = browser.sockets[0].sent
        if "resume_token" in first_reply:
            browser.take_credentials(first_reply)
        return browser

    async def close_all(self):
        for socket, task in self.tasks.items():
            if not task.done():
                socket.push(disconnect_frame(1005))
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)


@pytest_asyncio.fixture
async def link(handler):
    link = Link(handler)
    yield link
    await link.close_all()


async def host_and_guest(link):
    host = await link.join("host")
    guest = await link.join("guest", host.socket.sent[0]["room_id"])
    return host, guest


class TestParkingAndWindow:
    @pytest.mark.asyncio
    async def test_resume_window_is_shorter_than_the_host_grace(self):
        assert RESUME_WINDOW_SECONDS < HOST_GRACE_SECONDS

    @pytest.mark.asyncio
    async def test_dropped_guest_keeps_its_seat_and_tells_nobody(self, link, room_manager):
        host, guest = await host_and_guest(link)
        heard_before = len(host.socket.sent)

        await guest.drop(1006)

        assert len(host.socket.sent) == heard_before
        assert room_manager.get_room(host.socket.sent[0]["room_id"]).participant_count == 2

    @pytest.mark.asyncio
    async def test_dropped_host_keeps_its_seat_and_the_guest_hears_neither_away_nor_left(self, link):
        host, guest = await host_and_guest(link)
        heard_before = len(guest.socket.sent)

        await host.drop(1006)

        assert len(guest.socket.sent) == heard_before

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", [1000, 1001, 1005])
    async def test_guest_closing_normally_leaves_at_once(self, link, code):
        host, guest = await host_and_guest(link)

        await guest.drop(code)

        assert host.frames(SignalType.PARTICIPANT_LEFT) == [
            {"type": SignalType.PARTICIPANT_LEFT.value, "participant_id": guest.peer_id, "seq": 3}
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", [1000, 1001, 1005])
    async def test_host_closing_normally_leaves_at_once_with_the_full_grace(self, link, code):
        host, guest = await host_and_guest(link)

        await host.drop(code)

        [away] = guest.frames(SignalType.HOST_AWAY)
        assert away["seconds_left"] == HOST_GRACE_SECONDS
        assert guest.frames(SignalType.PARTICIPANT_LEFT)

    @pytest.mark.asyncio
    async def test_socket_that_fails_with_an_exception_leaves_at_once(self, link):
        host, guest = await host_and_guest(link)
        guest.socket.inbox.put_nowait(None)

        with pytest.raises(TypeError):
            await link.tasks[guest.socket]

        assert host.frames(SignalType.PARTICIPANT_LEFT)

    @pytest.mark.asyncio
    async def test_abnormal_close_of_a_socket_in_no_room_leaves_at_once(self, link, handler):
        socket = await link.open(account_named("stranger"))

        socket.push(disconnect_frame(1006))
        await link.tasks[socket]

        assert handler._peers == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("silent_seconds", [5, RESUME_WINDOW_SECONDS - 0.1])
    async def test_guest_resuming_inside_the_window_keeps_its_seat_and_the_others_hear_nothing(
        self, link, scheduler, silent_seconds
    ):
        host, guest = await host_and_guest(link)
        old_token = guest.token
        await guest.drop()
        await scheduler.advance(silent_seconds)
        heard_before = len(host.socket.sent)

        await guest.resume()

        [resumed] = guest.frames(SignalType.RESUMED)
        assert resumed["resume_token"] != old_token
        assert len(host.socket.sent) == heard_before
        await scheduler.advance(RESUME_WINDOW_SECONDS)
        assert host.frames(SignalType.PARTICIPANT_LEFT) == []

    @pytest.mark.asyncio
    async def test_window_running_out_removes_the_seat_for_real_and_a_late_resume_is_refused(
        self, link, scheduler, room_manager
    ):
        host, guest = await host_and_guest(link)
        await guest.drop()

        await scheduler.advance(RESUME_WINDOW_SECONDS - 1)
        assert host.frames(SignalType.PARTICIPANT_LEFT) == []
        await scheduler.advance(1)
        await guest.resume()

        [left] = host.frames(SignalType.PARTICIPANT_LEFT)
        assert left["participant_id"] == guest.peer_id
        assert guest.frames(SignalType.ERROR)[0]["reason"] == ErrorReason.RESUME_REFUSED.value
        assert room_manager.get_room(host.socket.sent[0]["room_id"]).participant_ids == [host.peer_id]

    @pytest.mark.asyncio
    async def test_resume_after_the_timer_fired_but_before_its_callback_ran_keeps_the_seat(
        self, link, scheduler, room_manager
    ):
        host, guest = await host_and_guest(link)
        await guest.drop()
        [window_expiry] = [timer for timer in scheduler._timers if timer.pending]
        window_expiry.pending = False

        await guest.resume()
        await window_expiry.callback()

        assert host.frames(SignalType.PARTICIPANT_LEFT) == []
        assert guest.peer_id in room_manager.get_room(host.socket.sent[0]["room_id"]).participant_ids

    @pytest.mark.asyncio
    async def test_stale_expiry_of_an_earlier_park_does_nothing_and_the_new_window_runs_its_full_length(
        self, link, scheduler
    ):
        host, guest = await host_and_guest(link)
        await guest.drop()
        [first_expiry] = [timer for timer in scheduler._timers if timer.pending]
        await guest.resume()
        await guest.accept_resumption()
        await guest.drop()

        await first_expiry.callback()
        await scheduler.advance(RESUME_WINDOW_SECONDS - 1)
        assert host.frames(SignalType.PARTICIPANT_LEFT) == []
        await scheduler.advance(1)

        assert len(host.frames(SignalType.PARTICIPANT_LEFT)) == 1

    @pytest.mark.asyncio
    async def test_parked_guest_still_counts_toward_the_room_limit(self, link):
        host = await link.join("host")
        room_id = host.socket.sent[0]["room_id"]
        guests = [await link.join(f"guest{index}", room_id) for index in range(ROOM_LIMIT - 1)]
        await guests[0].drop()

        latecomer = await link.join("latecomer", room_id)

        assert latecomer.frames(SignalType.ERROR)[0]["reason"] == ErrorReason.FULL.value


class TestParkedHost:
    @pytest.mark.asyncio
    async def test_host_unseen_for_the_window_is_away_with_the_rest_of_the_grace_and_the_call_ends_a_minute_on(
        self, link, scheduler, room_manager
    ):
        host, guest = await host_and_guest(link)
        await host.drop()

        await scheduler.advance(RESUME_WINDOW_SECONDS)
        [away] = guest.frames(SignalType.HOST_AWAY)
        assert away["seconds_left"] == HOST_GRACE_SECONDS - RESUME_WINDOW_SECONDS
        assert [frame["type"] for frame in guest.socket.sent[-2:]] == [
            SignalType.HOST_AWAY.value,
            SignalType.PARTICIPANT_LEFT.value,
        ]
        await scheduler.advance(HOST_GRACE_SECONDS - RESUME_WINDOW_SECONDS - 1)
        assert guest.frames(SignalType.ROOM_CLOSED) == []
        await scheduler.advance(1)

        [closed] = guest.frames(SignalType.ROOM_CLOSED)
        assert closed["reason"] == RoomClosedReason.HOST_GONE.value
        assert room_manager.room_count == 0

    @pytest.mark.asyncio
    async def test_host_resuming_inside_the_window_was_never_away(self, link, scheduler, room_manager):
        host, guest = await host_and_guest(link)
        await host.drop()
        await scheduler.advance(20)
        heard_before = len(guest.socket.sent)

        await host.resume()
        await scheduler.advance(2 * HOST_GRACE_SECONDS)

        assert len(guest.socket.sent) == heard_before
        assert room_manager.room_count == 1

    @pytest.mark.asyncio
    async def test_host_account_joining_fresh_after_the_window_is_host_again_and_the_guest_hears_host_back(
        self, link, scheduler
    ):
        host, guest = await host_and_guest(link)
        await host.drop()
        await scheduler.advance(45)

        returning = await link.join("host", host.socket.sent[0]["room_id"])

        assert returning.frames(SignalType.EXISTING_PARTICIPANTS)[0]["is_host"] is True
        assert [frame["type"] for frame in guest.socket.sent[-2:]] == [
            SignalType.PARTICIPANT_JOINED.value,
            SignalType.HOST_BACK.value,
        ]

    @pytest.mark.asyncio
    async def test_room_ended_while_a_guest_is_parked_is_told_to_it_on_its_resume(self, link, scheduler):
        host, guest = await host_and_guest(link)
        await guest.drop()

        await host.say({"type": SignalType.END_ROOM.value})
        await guest.resume()

        assert guest.frames(SignalType.ROOM_CLOSED) == [
            {"type": SignalType.ROOM_CLOSED.value, "reason": RoomClosedReason.ENDED.value, "seq": 2}
        ]


REFUSED = ErrorReason.RESUME_REFUSED.value


def only_error_reason(browser):
    return [frame["reason"] for frame in browser.frames(SignalType.ERROR)]


class TestResumeRefusals:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "overrides",
        [
            pytest.param({"participant_id": "nobody"}, id="unknown_seat"),
            pytest.param({"token": "wrong"}, id="wrong_token"),
            pytest.param({"participant_id": 7}, id="non_string_participant_id"),
            pytest.param({"token": 7}, id="non_string_token"),
            pytest.param({"token": "tökén"}, id="non_ascii_token"),
            pytest.param({"token": "\ud800"}, id="unencodable_token"),
            pytest.param({"last_seq": None}, id="null_last_seq"),
            pytest.param({"last_seq": "1"}, id="string_last_seq"),
            pytest.param({"last_seq": 1.5}, id="float_last_seq"),
            pytest.param({"last_seq": True}, id="bool_last_seq"),
            pytest.param({"last_seq": -1}, id="negative_last_seq"),
            pytest.param({"sent_floor": None}, id="null_sent_floor"),
            pytest.param({"sent_floor": False}, id="bool_sent_floor"),
            pytest.param({"sent_floor": -1}, id="negative_sent_floor"),
        ],
    )
    async def test_unusable_credentials_are_refused_with_one_reason_and_the_seat_stays_parked(
        self, link, room_manager, scheduler, overrides
    ):
        _host, guest = await host_and_guest(link)
        await guest.drop()

        await guest.resume(**overrides)

        assert only_error_reason(guest) == [REFUSED]
        await guest.resume()
        assert guest.frames(SignalType.RESUMED)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("missing", ["participant_id", "token", "last_seq", "sent_floor"])
    async def test_resume_missing_a_field_is_refused(self, link, missing):
        _host, guest = await host_and_guest(link)
        await guest.drop()
        guest.sockets.append(await link.open(account_named("guest")))
        frame = guest.resume_frame()
        del frame[missing]

        await guest.send_raw(frame)

        assert only_error_reason(guest) == [REFUSED]

    @pytest.mark.asyncio
    async def test_right_token_from_another_account_is_refused_and_the_owner_can_still_resume(self, link):
        _host, guest = await host_and_guest(link)
        await guest.drop()

        await guest.resume(account=account_named("thief"))
        assert only_error_reason(guest) == [REFUSED]
        await guest.resume()

        assert guest.frames(SignalType.RESUMED)

    @pytest.mark.asyncio
    async def test_right_token_from_the_same_account_id_under_another_display_name_resumes(self, link):
        _host, guest = await host_and_guest(link)
        await guest.drop()

        await guest.resume(account=Account(id="guest", name="Guest, renamed"))

        assert guest.frames(SignalType.RESUMED)

    @pytest.mark.asyncio
    async def test_token_rotated_out_by_a_resume_is_refused(self, link):
        _host, guest = await host_and_guest(link)
        await guest.drop()
        await guest.resume()
        stale_token = guest.token
        await guest.accept_resumption()
        await guest.drop()

        await guest.resume(token=stale_token)

        assert only_error_reason(guest) == [REFUSED]

    @pytest.mark.asyncio
    async def test_resume_as_a_later_frame_from_another_seat_is_refused_and_the_parked_seat_still_resumes(self, link):
        host, guest = await host_and_guest(link)
        await guest.drop()
        other_seat = await link.join("guest", host.socket.sent[0]["room_id"])
        heard_before = len(host.socket.sent)

        await other_seat.say(guest.resume_frame())

        assert only_error_reason(other_seat) == [REFUSED]
        assert len(host.socket.sent) == heard_before
        await guest.resume()
        assert guest.frames(SignalType.RESUMED)

    @pytest.mark.asyncio
    async def test_refusal_names_no_cause_and_echoes_no_credential(self, link):
        _host, guest = await host_and_guest(link)
        await guest.drop()

        await guest.resume(**{"token": "guessed"})

        [refusal] = guest.socket.sent
        assert refusal == {"type": SignalType.ERROR.value, "reason": REFUSED, "message": "Resume refused"}


@dataclass
class HeldReplay:
    reached: asyncio.Event
    release: asyncio.Event


async def resume_holding_the_first_replayed_frame(link, browser):
    held = HeldReplay(reached=asyncio.Event(), release=asyncio.Event())
    socket = await link.open(account_named(browser.name))
    write = socket.send_text

    async def hold_replayed_frames(text):
        if socket.sent:
            held.reached.set()
            await held.release.wait()
        await write(text)

    socket.send_text = hold_replayed_frames
    browser.sockets.append(socket)
    socket.push(text_frame(browser.resume_frame()))
    return held


async def fill_server_log(host, guest, frames):
    for index in range(frames):
        await host.say({"type": SignalType.CHAT.value, "message": f"message {index}"})


class TestResumeLogs:
    @pytest.mark.asyncio
    async def test_server_log_that_no_longer_reaches_the_browsers_last_frame_refuses_and_releases_the_seat(self, link):
        host, guest = await host_and_guest(link)
        await guest.drop()
        await fill_server_log(host, guest, REPLAY_LOG_LIMIT + 5)

        await guest.resume(last_seq=0)

        assert only_error_reason(guest) == [REFUSED]
        [left] = host.frames(SignalType.PARTICIPANT_LEFT)
        assert left["participant_id"] == guest.peer_id

    @pytest.mark.asyncio
    async def test_browser_log_that_starts_beyond_what_the_server_received_refuses_and_releases_the_seat(self, link):
        host, guest = await host_and_guest(link)
        await guest.drop()

        await guest.resume(sent_floor=1)

        assert only_error_reason(guest) == [REFUSED]
        assert host.frames(SignalType.PARTICIPANT_LEFT)

    @pytest.mark.asyncio
    async def test_releasing_an_unresumable_seat_closes_the_old_socket_that_is_still_attached(self, link):
        host, guest = await host_and_guest(link)
        old_socket = guest.socket

        await guest.resume(sent_floor=1)

        assert old_socket.closed_with == SUPERSEDED_CLOSE_CODE
        assert host.frames(SignalType.PARTICIPANT_LEFT)
        old_socket.push(disconnect_frame(1006))
        await link.tasks[old_socket]
        assert len(host.frames(SignalType.PARTICIPANT_LEFT)) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("claimed_extra", [0, 1])
    async def test_gaps_left_by_superseded_frames_do_not_stop_a_resume_and_only_the_newest_description_comes_back(
        self, link, claimed_extra
    ):
        host, guest = await host_and_guest(link)
        await guest.drop()
        seen = guest.received_seq
        await host.say({"type": SignalType.OFFER.value, "target": guest.peer_id, "sdp": "first"})
        await host.say({"type": SignalType.OFFER.value, "target": guest.peer_id, "sdp": "second"})

        await guest.resume(last_seq=seen + claimed_extra)

        assert [frame["sdp"] for frame in guest.frames(SignalType.OFFER)] == ["second"]

    @pytest.mark.asyncio
    async def test_candidates_before_a_newer_description_are_dropped_and_the_ones_after_it_replayed(self, link):
        host, guest = await host_and_guest(link)
        await guest.drop()
        target = guest.peer_id
        await host.say({"type": SignalType.OFFER.value, "target": target, "sdp": "old"})
        await host.say({"type": SignalType.ICE_CANDIDATE.value, "target": target, "candidate": "old-candidate"})
        await host.say({"type": SignalType.OFFER.value, "target": target, "sdp": "new"})
        await host.say({"type": SignalType.ICE_CANDIDATE.value, "target": target, "candidate": "new-candidate"})

        await guest.resume()

        replayed = guest.frames(SignalType.OFFER, SignalType.ICE_CANDIDATE)
        assert [frame.get("sdp") or frame["candidate"] for frame in replayed] == ["new", "new-candidate"]

    @pytest.mark.asyncio
    async def test_newcomer_offer_and_chat_missed_while_parked_are_replayed_in_order_after_resumed(self, link):
        host, guest = await host_and_guest(link)
        await guest.drop()
        newcomer = await link.join("newcomer", host.socket.sent[0]["room_id"])
        await newcomer.say({"type": SignalType.OFFER.value, "target": guest.peer_id, "sdp": "hello"})
        await host.say({"type": SignalType.CHAT.value, "message": "hi"})

        await guest.resume()

        assert [frame["type"] for frame in guest.socket.sent] == [
            SignalType.RESUMED.value,
            SignalType.PARTICIPANT_JOINED.value,
            SignalType.OFFER.value,
            SignalType.CHAT.value,
        ]
        assert [frame["seq"] for frame in guest.socket.sent[1:]] == sorted(
            frame["seq"] for frame in guest.socket.sent[1:]
        )
        assert host.socket.sent[-1]["type"] != SignalType.CHAT.value

    @pytest.mark.asyncio
    async def test_parked_seat_is_written_nothing_while_it_is_away(self, link):
        host, guest = await host_and_guest(link)
        dropped_socket = guest.socket
        await guest.drop()
        written_before = len(dropped_socket.sent)

        await host.say({"type": SignalType.CHAT.value, "message": "hi"})

        assert len(dropped_socket.sent) == written_before

    @pytest.mark.asyncio
    async def test_frame_sent_while_a_replayed_frame_is_being_written_arrives_after_the_replayed_ones(self, link):
        host, guest = await host_and_guest(link)
        await guest.drop()
        await host.say({"type": SignalType.CHAT.value, "message": "missed 1"})
        await host.say({"type": SignalType.CHAT.value, "message": "missed 2"})
        writing_replay = await resume_holding_the_first_replayed_frame(link, guest)

        await writing_replay.reached.wait()
        await host.say({"type": SignalType.CHAT.value, "message": "live"})
        writing_replay.release.set()
        await link.settle(guest.socket)

        assert [frame["message"] for frame in guest.frames(SignalType.CHAT)] == ["missed 1", "missed 2", "live"]
        seqs = [frame["seq"] for frame in guest.socket.sent[1:]]
        assert seqs == sorted(set(seqs))

    @pytest.mark.asyncio
    async def test_log_overrun_while_a_replayed_frame_is_being_written_releases_the_seat_instead_of_skipping_frames(
        self, link
    ):
        host, guest = await host_and_guest(link)
        await guest.drop()
        await host.say({"type": SignalType.CHAT.value, "message": "missed"})
        writing_replay = await resume_holding_the_first_replayed_frame(link, guest)

        await writing_replay.reached.wait()
        await fill_server_log(host, guest, REPLAY_LOG_LIMIT + 5)
        writing_replay.release.set()
        await link.settle(guest.socket)

        assert [frame["message"] for frame in guest.frames(SignalType.CHAT)] == ["missed"]
        assert guest.socket.closed_with == SUPERSEDED_CLOSE_CODE
        [left] = host.frames(SignalType.PARTICIPANT_LEFT)
        assert left["participant_id"] == guest.peer_id

    @pytest.mark.asyncio
    async def test_superseded_replay_overrunning_the_log_leaves_the_seat_of_the_newer_resume_alone(self, link):
        host, guest = await host_and_guest(link)
        await guest.drop()
        await host.say({"type": SignalType.CHAT.value, "message": "missed"})
        writing_replay = await resume_holding_the_first_replayed_frame(link, guest)
        await writing_replay.reached.wait()
        superseded_socket = guest.socket
        await guest.accept_resumption()
        newer_socket = await guest.resume()
        await fill_server_log(host, guest, REPLAY_LOG_LIMIT + 5)

        writing_replay.release.set()
        await link.settle(superseded_socket)

        assert newer_socket.closed_with is None
        assert host.frames(SignalType.PARTICIPANT_LEFT) == []
        await host.say({"type": SignalType.CHAT.value, "message": "live"})
        assert newer_socket.sent[-1]["message"] == "live"

    @pytest.mark.asyncio
    async def test_replay_stops_when_the_socket_breaks_and_the_next_resume_gets_the_rest(self, link):
        host, guest = await host_and_guest(link)
        await guest.drop()
        await host.say({"type": SignalType.CHAT.value, "message": "missed"})
        failing = await link.open(account_named("guest"))
        deliver_resumed = failing.send_text

        async def break_after_the_first_frame(text):
            if failing.sent:
                raise WebSocketDisconnect(code=1006)
            await deliver_resumed(text)

        failing.send_text = break_after_the_first_frame
        guest.sockets.append(failing)
        await guest.send_raw(guest.resume_frame())
        await guest.accept_resumption()

        await guest.drop()
        await guest.resume()

        assert [frame["message"] for frame in guest.frames(SignalType.CHAT)] == ["missed"]


class TestResumeSupersedesTheOldSocket:
    @pytest.mark.asyncio
    async def test_resume_closes_the_old_socket_and_nothing_it_says_or_does_afterwards_counts(self, link):
        host, guest = await host_and_guest(link)
        old_socket = guest.socket
        await guest.resume()
        heard_before = len(host.socket.sent)

        old_socket.push(text_frame({"type": SignalType.CHAT.value, "message": "ghost", "seq": 1}))
        old_socket.push(disconnect_frame(1006))
        await link.tasks[old_socket]

        assert old_socket.closed_with == SUPERSEDED_CLOSE_CODE
        assert len(host.socket.sent) == heard_before
        await guest.say({"type": SignalType.CHAT.value, "message": "from the new socket"})
        assert host.socket.sent[-1]["message"] == "from the new socket"

    @pytest.mark.asyncio
    async def test_resume_while_the_old_socket_is_already_gone_closes_nothing(self, link):
        _host, guest = await host_and_guest(link)
        old_socket = guest.socket
        await guest.drop()

        await guest.resume()

        assert old_socket.closed_with is None

    @pytest.mark.asyncio
    async def test_old_socket_that_fails_to_close_does_not_stop_the_resume(self, link):
        _host, guest = await host_and_guest(link)

        async def broken_close(code):
            raise WebSocketDisconnect(code=code)

        guest.socket.close = broken_close
        await guest.resume()

        assert guest.frames(SignalType.RESUMED)


class TestSequenceNumbers:
    @pytest.mark.asyncio
    async def test_frames_to_a_seat_carry_rising_sequence_numbers_including_its_credentials(self, link):
        host, guest = await host_and_guest(link)
        await host.say({"type": SignalType.CHAT.value, "message": "hi"})

        assert [frame["seq"] for frame in guest.socket.sent] == [1, 2]
        assert host.socket.sent[0]["seq"] == 1

    @pytest.mark.asyncio
    async def test_frame_resent_at_or_below_the_received_number_is_not_relayed_twice(self, link):
        host, guest = await host_and_guest(link)
        chat = {"type": SignalType.CHAT.value, "message": "once"}
        await guest.say(chat)

        await guest.send_raw({**chat, "seq": 1})

        assert [frame["message"] for frame in host.frames(SignalType.CHAT)] == ["once"]

    @pytest.mark.asyncio
    async def test_frame_swallowed_before_the_cut_is_relayed_once_when_the_browser_resends_it_after_the_resume(
        self, link
    ):
        host, guest = await host_and_guest(link)
        await guest.drop()
        guest.sent_count += 1

        await guest.resume()
        [resumed] = guest.frames(SignalType.RESUMED)
        assert resumed["last_seq"] == 0
        await guest.send_raw(
            {"type": SignalType.ANSWER.value, "target": host.peer_id, "sdp": "swallowed", "seq": guest.sent_count}
        )

        assert [frame["sdp"] for frame in host.frames(SignalType.ANSWER)] == ["swallowed"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("seq", [pytest.param(None, id="missing"), "1", 1.5, True, -1])
    async def test_later_frame_without_a_usable_sequence_number_is_answered_invalid_message(self, link, seq):
        host, guest = await host_and_guest(link)
        frame = {"type": SignalType.CHAT.value, "message": "hi"}

        await guest.send_raw(frame if seq is None else {**frame, "seq": seq})

        assert only_error_reason(guest) == [ErrorReason.INVALID_MESSAGE.value]
        assert host.frames(SignalType.CHAT) == []


def chat_frame_of_length(length, seq):
    unpadded = json.dumps({"type": SignalType.CHAT.value, "message": "", "seq": seq})
    return {"type": SignalType.CHAT.value, "message": "x" * (length - len(unpadded)), "seq": seq}


class TestFrameLength:
    @pytest.mark.asyncio
    async def test_frame_of_exactly_the_bound_is_relayed(self, link):
        host, guest = await host_and_guest(link)

        await guest.send_raw(chat_frame_of_length(MAX_SIGNALING_FRAME_LENGTH, seq=1))

        assert len(host.frames(SignalType.CHAT)) == 1

    @pytest.mark.asyncio
    async def test_frame_longer_than_the_bound_is_refused_and_neither_relayed_nor_replayed(self, link):
        host, guest = await host_and_guest(link)

        await guest.send_raw(chat_frame_of_length(MAX_SIGNALING_FRAME_LENGTH + 1, seq=1))

        assert only_error_reason(guest) == [ErrorReason.INVALID_MESSAGE.value]
        assert host.frames(SignalType.CHAT) == []
        await host.drop()
        await host.resume()
        assert host.frames(SignalType.CHAT) == []


class TestTokenStaysSecret:
    @pytest.mark.asyncio
    async def test_no_log_value_of_a_park_a_resume_or_a_refusal_holds_a_token(self, link):
        host, guest = await host_and_guest(link)
        first_token = guest.token

        with structlog.testing.capture_logs() as logs:
            await guest.drop()
            await guest.resume()
            await guest.accept_resumption()
            await guest.drop()
            await guest.resume(token=first_token)

        assert {"seat_parked", "seat_resumed", "resume_refused"} <= {log["event"] for log in logs}
        for secret in (first_token, guest.token, host.token):
            assert secret not in json.dumps(logs, default=str)

    @pytest.mark.asyncio
    async def test_no_frame_to_another_member_carries_a_token_or_another_members_peer_credentials(self, link):
        host, guest = await host_and_guest(link)
        await guest.drop()
        await guest.resume()
        await guest.accept_resumption()
        await guest.say({"type": SignalType.CHAT.value, "message": "hi"})

        heard_by_host = json.dumps(host.socket.sent[1:])
        assert guest.token not in heard_by_host
        assert "resume_token" not in heard_by_host
        assert host.token not in json.dumps(guest.sockets[-1].sent)

    @pytest.mark.asyncio
    async def test_seat_reprs_hide_the_token(self):
        seat = Peer(account=ADMITTED_ACCOUNT, websocket=make_ws_mock())

        assert seat.resume_token not in repr(seat)
