import json

import pytest
import structlog
import structlog.testing
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from hopin.config.constants import (
    HOST_GRACE_SECONDS,
    ErrorReason,
    RoomClosedReason,
    SignalType,
)
from hopin.signaling.handler import Connection, Peer, SignalingHandler
from tests.unit.conftest import (
    ADMITTED_ACCOUNT,
    ROOM_LIMIT,
    FakeSocket,
    ManualScheduler,
    SocketAccountResolver,
    account_named,
    disconnect_frame,
    make_ws_mock,
    text_frame,
)

pytestmark = pytest.mark.asyncio


class ResolverError(Exception):
    pass


class RaisingIdentityResolver:
    async def resolve(self, _websocket):
        raise ResolverError


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

    @pytest.mark.parametrize(
        ("raw_data", "detail"),
        [
            pytest.param("not json", "Invalid JSON", id="not_json"),
            pytest.param("[]", "Message must be a JSON object", id="json_array"),
            pytest.param({"foo": "bar"}, "Missing signal type", id="no_type"),
            pytest.param({"type": "unknown"}, "Unknown signal type: unknown", id="unknown_type"),
        ],
    )
    async def test_handle_message_invalid_frame_tells_the_person_what_was_wrong(self, handler, raw_data, detail):
        ws = register_peer(handler, "sender")

        await deliver(handler, "sender", raw_data)

        assert json.loads(ws.send_text.call_args[0][0])["message"] == detail

    async def test_handle_message_full_room_refusal_carries_the_capacity(self, handler, room_manager):
        ws = register_peer(handler, "sender")
        raw_data = join_full_room(handler, room_manager)

        await deliver(handler, "sender", raw_data)

        response = json.loads(ws.send_text.call_args[0][0])
        assert response["capacity"] == ROOM_LIMIT


class TestJoinCreateRoom:
    async def test_creating_a_room_answers_room_created_and_seats_the_creator(self, handler):
        ws = register_peer(handler, "host1")

        await deliver(handler, "host1", join_message())

        assert sent_types(ws) == [SignalType.ROOM_CREATED.value]
        assert handler._peer_rooms["host1"] == sent_frames(ws)[0]["room_id"]


class TestJoinExistingRoom:
    async def test_second_join_from_a_seat_in_a_room_is_refused_and_the_first_room_is_unchanged(
        self, handler, room_manager
    ):
        room, sockets = seat_in_room(handler, room_manager, "host1", "p1")
        other_room = room_manager.create_room("host2", account_named("host2"))

        await deliver(handler, "p1", join_message(other_room.room_id))
        await deliver(handler, "p1", join_message())

        assert [frame["reason"] for frame in frames_of_type(sockets["p1"], SignalType.ERROR)] == [
            ErrorReason.INVALID_MESSAGE.value
        ] * 2
        assert handler._peer_rooms["p1"] == room.room_id
        assert room.participant_ids == ["host1", "p1"]
        assert other_room.participant_ids == ["host2"]

    async def test_refused_second_join_is_not_logged_as_a_join(self, handler, room_manager):
        seat_in_room(handler, room_manager, "host1", "p1")

        with structlog.testing.capture_logs() as logs:
            await deliver(handler, "p1", join_message())

        assert [log["event"] for log in logs if log["event"] in ("room_created", "participant_joined")] == []

    async def test_joiner_gets_the_existing_participants_and_they_hear_of_the_joiner(self, handler, room_manager):
        room, sockets = seat_in_room(handler, room_manager, "host1")
        joiner_ws = register_peer(handler, "p1")

        await deliver(handler, "p1", join_message(room.room_id))

        [existing] = frames_of_type(joiner_ws, SignalType.EXISTING_PARTICIPANTS)
        assert "host1" in existing["participants"]
        assert sent_frames(sockets["host1"]) == [{"type": SignalType.PARTICIPANT_JOINED.value, "participant_id": "p1"}]


class TestRelay:
    @pytest.mark.parametrize(
        ("signal_type", "payload"),
        [
            (SignalType.OFFER, {"sdp": "test_sdp"}),
            (SignalType.ANSWER, {"sdp": "answer_sdp"}),
            (SignalType.ICE_CANDIDATE, {"candidate": "test_candidate"}),
        ],
    )
    async def test_signal_is_relayed_to_the_target_with_its_sender(self, handler, room_manager, signal_type, payload):
        _room, sockets = seat_in_room(handler, room_manager, "p1", "p2")

        await deliver(handler, "p1", {"type": signal_type.value, "target": "p2", **payload})

        [relayed] = sent_frames(sockets["p2"])
        assert {"type": signal_type.value, "from": "p1", **payload}.items() <= relayed.items()

    @pytest.mark.parametrize("signal_type", [SignalType.OFFER, SignalType.ANSWER, SignalType.ICE_CANDIDATE])
    @pytest.mark.parametrize(
        ("sender", "route"),
        [
            pytest.param("p1", {}, id="without_target"),
            pytest.param("p1", {"target": "outsider"}, id="to_a_peer_in_another_room"),
            pytest.param("p1", {"target": "loner"}, id="to_a_peer_in_no_room"),
            pytest.param("loner", {"target": "p1"}, id="from_a_sender_in_no_room"),
        ],
    )
    async def test_relay_that_has_no_recipient_in_the_senders_room_reaches_nobody(
        self, handler, room_manager, signal_type, sender, route
    ):
        _room, sockets = seat_in_room(handler, room_manager, "p1", "p2")
        _other_room, other_sockets = seat_in_room(handler, room_manager, "outsider")
        sockets["loner"] = register_peer(handler, "loner")

        await deliver(handler, sender, {"type": signal_type.value, **route})

        for ws in (*sockets.values(), *other_sockets.values()):
            ws.send_text.assert_not_called()


class TestDisconnect:
    async def test_guest_leaving_tells_the_others_without_host_away_and_the_room_stays(self, handler, room_manager):
        room, sockets = seat_in_room(handler, room_manager, "host1", "p1")

        await handler._handle_disconnect("p1")

        assert sent_frames(sockets["host1"]) == [{"type": SignalType.PARTICIPANT_LEFT.value, "participant_id": "p1"}]
        assert room_manager.has_room(room.room_id)

    async def test_last_guest_leaving_keeps_the_room(self, handler, room_manager):
        room, _sockets = seat_in_room(handler, room_manager, "host1", "p1")
        await handler._handle_disconnect("p1")
        await handler._handle_disconnect("host1")
        assert room_manager.has_room(room.room_id)

    async def test_disconnect_without_room_is_safe(self, handler):
        register_peer(handler, "c1")
        await handler._handle_disconnect("c1")
        assert "c1" not in handler._peers

    async def test_disconnect_from_deleted_room(self, handler):
        register_peer(handler, "p1")
        handler._peer_rooms["p1"] = "deleted_room"
        await handler._handle_disconnect("p1")
        assert "p1" not in handler._peer_rooms


class TestPeerInNoRoom:
    @pytest.mark.parametrize(
        "message",
        [
            pytest.param({"type": SignalType.LOCK_ROOM.value}, id="lock"),
            pytest.param({"type": SignalType.UNLOCK_ROOM.value}, id="unlock"),
            pytest.param({"type": SignalType.END_ROOM.value}, id="end_room"),
            pytest.param({"type": SignalType.CHAT.value, "message": "hello"}, id="chat"),
            pytest.param({"type": SignalType.MEDIA_STATE.value, "audio": True, "video": True}, id="media_state"),
        ],
    )
    async def test_room_request_from_a_peer_in_no_room_is_ignored(self, handler, message):
        ws = register_peer(handler, "c1")

        await deliver(handler, "c1", message)

        ws.send_text.assert_not_called()


class TestLockUnlock:
    async def test_host_can_lock_room(self, handler, room_manager):
        room, sockets = seat_in_room(handler, room_manager, "host1", "p1")

        await deliver(handler, "host1", {"type": SignalType.LOCK_ROOM.value})

        assert sent_types(sockets["host1"]) == [SignalType.ROOM_LOCKED.value]
        assert sent_types(sockets["p1"]) == [SignalType.ROOM_LOCKED.value]
        assert room.locked is True

    async def test_host_can_unlock_room(self, handler, room_manager):
        room, sockets = seat_in_room(handler, room_manager, "host1")
        room_manager.set_room_locked(room.room_id, account_named("host1"), locked=True)

        await deliver(handler, "host1", {"type": SignalType.UNLOCK_ROOM.value})

        assert sent_types(sockets["host1"]) == [SignalType.ROOM_UNLOCKED.value]
        assert room.locked is False


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
    async def test_abnormal_disconnect_is_logged(self, handler, room_manager):
        socket = FakeSocket([text_frame({"type": SignalType.JOIN.value}), disconnect_frame(1006)])
        with structlog.testing.capture_logs() as logs:
            await handler.handle_websocket(socket)

        [error_log] = [log for log in logs if log["event"] == "websocket_error"]
        assert error_log["code"] == 1006
        assert room_manager.room_count == 1

    @pytest.mark.parametrize("code", [1000, 1001])
    async def test_normal_disconnect_is_not_logged_as_error(self, handler, code):
        socket = FakeSocket([disconnect_frame(code)])
        with structlog.testing.capture_logs() as logs:
            await handler.handle_websocket(socket)

        assert [log for log in logs if log["event"] == "websocket_error"] == []

    async def test_binary_frame_is_ignored_and_later_text_frames_still_process(self, handler, room_manager):
        socket = FakeSocket(
            [
                {"type": "websocket.receive", "bytes": b"x"},
                text_frame({"type": SignalType.JOIN.value}),
                disconnect_frame(1000),
            ]
        )
        await handler.handle_websocket(socket)
        assert room_manager.room_count == 1

    async def test_refused_handshake_closes_with_policy_violation_without_accepting(self, room_manager):
        refusing_handler = SignalingHandler(room_manager, SocketAccountResolver(), ManualScheduler())
        socket = FakeSocket([], account=None)
        await refusing_handler.handle_websocket(socket)
        assert socket.closed_with == 1008
        assert not socket.accepted

    async def test_failing_resolver_propagates_without_accepting_the_socket(self, room_manager):
        failing_handler = SignalingHandler(room_manager, RaisingIdentityResolver(), ManualScheduler())
        socket = FakeSocket([])
        with pytest.raises(ResolverError):
            await failing_handler.handle_websocket(socket)
        assert not socket.accepted

    async def test_admitted_handshake_accepts_and_registers_a_peer_carrying_the_account(self, handler):
        socket = FakeSocket([disconnect_frame(1000)])
        peers_while_connected = await handle_and_capture_peers(handler, socket)

        assert socket.accepted
        assert [peer.account for peer in peers_while_connected.values()] == [ADMITTED_ACCOUNT]

    async def test_two_sockets_of_one_account_become_two_peers(self, handler):
        first = await handle_and_capture_peers(handler, FakeSocket([disconnect_frame(1000)]))
        second = await handle_and_capture_peers(handler, FakeSocket([disconnect_frame(1000)]))

        assert first.keys().isdisjoint(second.keys())


class TestChat:
    async def test_chat_broadcasts_to_other_participants(self, handler, room_manager):
        _room, sockets = seat_in_room(handler, room_manager, "host1", "p1")

        await deliver(handler, "host1", {"type": SignalType.CHAT.value, "message": "hello"})

        assert sent_frames(sockets["p1"]) == [{"type": SignalType.CHAT.value, "message": "hello", "from": "host1"}]
        sockets["host1"].send_text.assert_not_called()

    async def test_chat_empty_message_is_ignored(self, handler, room_manager):
        _room, sockets = seat_in_room(handler, room_manager, "host1", "p1")

        await deliver(handler, "host1", {"type": SignalType.CHAT.value, "message": ""})

        sockets["p1"].send_text.assert_not_called()


class TestMediaState:
    async def test_media_state_reaches_every_other_participant_with_from(self, handler, room_manager):
        _, sockets = seat_in_room(handler, room_manager, "host1", "p1", "p2")

        await deliver(handler, "p1", {"type": SignalType.MEDIA_STATE.value, "audio": False, "video": True})

        expected = {"type": SignalType.MEDIA_STATE.value, "from": "p1", "audio": False, "video": True}
        assert sent_frames(sockets["host1"]) == [expected]
        assert sent_frames(sockets["p2"]) == [expected]
        assert sockets["p1"].send_text.call_count == 0

    @pytest.mark.parametrize(
        "state",
        [
            pytest.param({"audio": "no", "video": True}, id="audio_not_boolean"),
            pytest.param({"audio": True, "video": 1}, id="video_not_boolean"),
            pytest.param({"audio": True}, id="video_missing"),
            pytest.param({}, id="both_missing"),
        ],
    )
    async def test_media_state_without_boolean_audio_and_video_is_answered_invalid_message(
        self, handler, room_manager, state
    ):
        _, sockets = seat_in_room(handler, room_manager, "host1", "p1")

        await deliver(handler, "p1", {"type": SignalType.MEDIA_STATE.value, **state})

        assert [frame["reason"] for frame in sent_frames(sockets["p1"])] == [ErrorReason.INVALID_MESSAGE.value]
        assert sockets["host1"].send_text.call_count == 0


class TestSend:
    async def test_send_to_connected_client(self, handler):
        ws = register_peer(handler, "c1")
        await handler._send("c1", {"type": "test"})
        ws.send_text.assert_called_once()

    async def test_send_to_unknown_client_is_safe(self, handler):
        await handler._send("unknown", {"type": "test"})

    @pytest.mark.parametrize("closed_state", ["client_state", "application_state"])
    async def test_send_to_a_peer_whose_socket_is_closed_sends_nothing(self, handler, closed_state):
        ws = register_peer(handler, "c1")
        setattr(ws, closed_state, WebSocketState.DISCONNECTED)
        await handler._send("c1", {"type": "test"})
        ws.send_text.assert_not_called()


class TestSendToGonePeer:
    async def test_relay_to_peer_whose_send_raises_disconnect_keeps_the_sender_running(self, handler, room_manager):
        _room, sockets = seat_in_room(handler, room_manager, "p1", "p2")
        sender_ws, gone_ws = sockets["p1"], sockets["p2"]
        gone_ws.send_text.side_effect = WebSocketDisconnect(code=1006)

        await deliver(handler, "p1", {"type": SignalType.OFFER.value, "target": "p2"})
        await deliver(handler, "p1", "not json")

        response = json.loads(sender_ws.send_text.call_args[0][0])
        assert response["reason"] == ErrorReason.INVALID_MESSAGE.value

    async def test_relay_to_missing_targets_through_handle_websocket_sends_the_sender_no_frame(self, handler):
        socket = FakeSocket(
            [
                text_frame({"type": SignalType.OFFER.value, "target": ""}, seq=1),
                text_frame({"type": SignalType.OFFER.value, "target": "nobody"}, seq=2),
                disconnect_frame(1000),
            ]
        )
        await handler.handle_websocket(socket)
        assert socket.sent == []

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
                disconnect_frame(1000),
            ]
        )
        await handler.handle_websocket(socket)
        errors = [reply["reason"] for reply in socket.sent if reply["type"] == SignalType.ERROR.value]
        assert errors == [ErrorReason.INVALID_MESSAGE.value]

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
    async def test_last_host_connection_dropping_sends_host_away_then_participant_left_and_keeps_the_room(
        self, handler, room_manager
    ):
        room, sockets = await drop_host(handler, room_manager, "p1")

        assert sent_frames(sockets["p1"]) == [
            {"type": SignalType.HOST_AWAY.value, "seconds_left": 60, "participant_id": "host1"},
            {"type": SignalType.PARTICIPANT_LEFT.value, "participant_id": "host1"},
        ]
        assert room_manager.has_room(room.room_id)

    async def test_room_survives_until_the_grace_runs_out_then_closes_for_every_peer_as_host_gone(
        self, handler, room_manager, scheduler
    ):
        room, sockets = await drop_host(handler, room_manager, "p1", "p2")

        await scheduler.advance(59)
        assert room_manager.has_room(room.room_id)
        await scheduler.advance(1)

        closed = {
            "type": SignalType.ROOM_CLOSED.value,
            "reason": RoomClosedReason.HOST_GONE.value,
            "grace_seconds": HOST_GRACE_SECONDS,
        }
        assert frames_of_type(sockets["p1"], SignalType.ROOM_CLOSED) == [closed]
        assert frames_of_type(sockets["p2"], SignalType.ROOM_CLOSED) == [closed]
        assert not room_manager.has_room(room.room_id)

    async def test_former_member_of_a_closed_room_cannot_lock_or_chat(self, handler, room_manager, scheduler):
        _room, sockets = await drop_host(handler, room_manager, "p1", "p2")
        await scheduler.advance(60)
        frames_before = {name: len(sent_frames(ws)) for name, ws in sockets.items()}

        stale_connection = Connection(sockets["p1"], "p1", frames_received=1)
        await handler._handle_message(stale_connection, json.dumps({"type": SignalType.LOCK_ROOM.value, "seq": 1}))
        await handler._handle_message(
            stale_connection, json.dumps({"type": SignalType.CHAT.value, "message": "hi", "seq": 2})
        )

        assert {name: len(sent_frames(ws)) for name, ws in sockets.items()} == frames_before

    async def test_room_of_a_host_who_was_alone_closes_when_the_grace_runs_out(self, handler, room_manager, scheduler):
        room, _sockets = await drop_host(handler, room_manager)

        assert room_manager.has_room(room.room_id)
        await scheduler.advance(60)

        assert not room_manager.has_room(room.room_id)

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

    async def test_host_who_was_alone_finds_the_room_on_reload(self, handler, room_manager, scheduler):
        room, _sockets = await drop_host(handler, room_manager)

        returning_ws = await host_rejoins(handler, room.room_id)
        await scheduler.advance(60)

        assert frames_of_type(returning_ws, SignalType.EXISTING_PARTICIPANTS)[0]["is_host"] is True
        assert room_manager.has_room(room.room_id)

    async def test_host_account_is_admitted_past_the_lock_during_the_grace(self, handler, room_manager):
        room, sockets = await drop_host(handler, room_manager, "p1")
        room.locked = True

        returning_ws = await host_rejoins(handler, room.room_id)

        assert frames_of_type(returning_ws, SignalType.EXISTING_PARTICIPANTS)[0]["locked"] is True
        assert frames_of_type(sockets["p1"], SignalType.HOST_BACK)

    async def test_host_account_returns_into_its_reserved_seat_of_an_otherwise_full_room(self, handler, room_manager):
        guests = [f"g{index}" for index in range(ROOM_LIMIT - 1)]
        room, _sockets = await drop_host(handler, room_manager, *guests)
        stranger_ws = register_peer(handler, "stranger")
        await deliver(handler, "stranger", join_message(room.room_id))

        returning_ws = await host_rejoins(handler, room.room_id)

        assert frames_of_type(stranger_ws, SignalType.ERROR)[0]["reason"] == ErrorReason.FULL.value
        assert frames_of_type(returning_ws, SignalType.EXISTING_PARTICIPANTS)

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

    async def test_host_with_two_connections_keeps_the_room_hosted_when_one_drops(self, handler, room_manager):
        room, sockets = seat_in_room(handler, room_manager, "host1", "p1")
        second_ws = await host_rejoins(handler, room.room_id, "host-second")

        await handler._handle_disconnect("host1")
        await deliver(handler, "host-second", {"type": SignalType.LOCK_ROOM.value})

        assert frames_of_type(sockets["p1"], SignalType.HOST_AWAY) == []
        assert frames_of_type(second_ws, SignalType.ROOM_LOCKED)
        assert frames_of_type(second_ws, SignalType.EXISTING_PARTICIPANTS)[0]["is_host"] is True

    @pytest.mark.parametrize("signal_type", [SignalType.LOCK_ROOM, SignalType.UNLOCK_ROOM])
    async def test_lock_and_unlock_during_the_grace_answer_not_host_and_leave_the_lock_alone(
        self, handler, room_manager, signal_type
    ):
        room, sockets = await drop_host(handler, room_manager, "p1")

        await deliver(handler, "p1", {"type": signal_type.value})

        assert frames_of_type(sockets["p1"], SignalType.ERROR)[0]["reason"] == ErrorReason.NOT_HOST.value
        assert room.locked is False


class TestEndRoom:
    async def test_host_ending_the_room_closes_it_as_ended_for_every_peer_including_the_host(
        self, handler, room_manager
    ):
        room, sockets = seat_in_room(handler, room_manager, "host1", "p1")

        await deliver(handler, "host1", {"type": SignalType.END_ROOM.value})

        closed = {"type": SignalType.ROOM_CLOSED.value, "reason": RoomClosedReason.ENDED.value}
        assert frames_of_type(sockets["host1"], SignalType.ROOM_CLOSED) == [closed]
        assert frames_of_type(sockets["p1"], SignalType.ROOM_CLOSED) == [closed]
        assert not room_manager.has_room(room.room_id)


class TestExpiryRace:
    async def test_expiry_after_the_host_returned_does_nothing(self, handler, room_manager):
        room, sockets = await drop_host(handler, room_manager, "p1")
        await host_rejoins(handler, room.room_id)

        await handler._expire_grace(room.room_id)

        assert frames_of_type(sockets["p1"], SignalType.ROOM_CLOSED) == []
        assert room_manager.has_room(room.room_id)
