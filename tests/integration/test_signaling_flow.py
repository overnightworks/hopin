from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hopin.app import create_app
from hopin.config.constants import ErrorReason, SignalType
from hopin.config.settings import ServerSettings


@pytest.fixture
def client():
    settings = ServerSettings(max_participants_per_room=3)
    return TestClient(create_app(settings))


@pytest.fixture
def connect(client):
    with ExitStack() as sessions:
        yield lambda connection_id: sessions.enter_context(client.websocket_connect(f"/ws?id={connection_id}"))


@pytest.mark.integration
class TestConnect:
    def test_connect_without_id_closes_the_socket(self, client):
        with client.websocket_connect("/ws") as ws, pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1000


@pytest.mark.integration
class TestRoomCreation:
    def test_create_room_returns_room_id(self, connect):
        ws = connect("host1")
        ws.send_json({"type": SignalType.JOIN.value})
        response = ws.receive_json()
        assert response["type"] == SignalType.ROOM_CREATED.value
        assert "room_id" in response
        assert response["is_host"] is True

    def test_create_room_ignores_a_password_key(self, connect):
        ws = connect("host1")
        ws.send_json({"type": SignalType.JOIN.value, "password": "secret"})
        response = ws.receive_json()
        assert response["type"] == SignalType.ROOM_CREATED.value
        assert response["is_host"] is True


@pytest.mark.integration
class TestJoining:
    def test_join_existing_room(self, connect):
        host_ws = connect("host1")
        host_ws.send_json({"type": SignalType.JOIN.value})
        created = host_ws.receive_json()
        room_id = created["room_id"]

        p1_ws = connect("p1")
        p1_ws.send_json({"type": SignalType.JOIN.value, "room_id": room_id})

        p1_response = p1_ws.receive_json()
        assert p1_response["type"] == SignalType.EXISTING_PARTICIPANTS.value
        assert "host1" in p1_response["participants"]
        assert p1_response["is_host"] is False

        host_notification = host_ws.receive_json()
        assert host_notification["type"] == SignalType.PARTICIPANT_JOINED.value
        assert host_notification["participant_id"] == "p1"

    def test_join_nonexistent_room_sends_error(self, connect):
        ws = connect("p1")
        ws.send_json({"type": SignalType.JOIN.value, "room_id": "nonexistent"})
        response = ws.receive_json()
        assert response["type"] == SignalType.ERROR.value
        assert response["reason"] == ErrorReason.NOT_FOUND.value

    def test_full_room_sends_error(self, connect):
        host_ws = connect("host1")
        host_ws.send_json({"type": SignalType.JOIN.value})
        created = host_ws.receive_json()
        room_id = created["room_id"]

        p1_ws = connect("p1")
        p1_ws.send_json({"type": SignalType.JOIN.value, "room_id": room_id})
        p1_ws.receive_json()
        host_ws.receive_json()

        p2_ws = connect("p2")
        p2_ws.send_json({"type": SignalType.JOIN.value, "room_id": room_id})
        p2_ws.receive_json()
        host_ws.receive_json()
        p1_ws.receive_json()

        p3_ws = connect("p3")
        p3_ws.send_json({"type": SignalType.JOIN.value, "room_id": room_id})
        response = p3_ws.receive_json()
        assert response["type"] == SignalType.ERROR.value
        assert response["reason"] == ErrorReason.FULL.value


@pytest.mark.integration
class TestRelay:
    def test_offer_relayed_between_peers(self, connect):
        host_ws = connect("host1")
        host_ws.send_json({"type": SignalType.JOIN.value})
        created = host_ws.receive_json()
        room_id = created["room_id"]

        p1_ws = connect("p1")
        p1_ws.send_json({"type": SignalType.JOIN.value, "room_id": room_id})
        p1_ws.receive_json()
        host_ws.receive_json()

        p1_ws.send_json(
            {
                "type": SignalType.OFFER.value,
                "target": "host1",
                "sdp": "test_sdp_offer",
            },
        )

        relayed = host_ws.receive_json()
        assert relayed["type"] == SignalType.OFFER.value
        assert relayed["sdp"] == "test_sdp_offer"
        assert relayed["from"] == "p1"


@pytest.mark.integration
class TestLockUnlock:
    def test_lock_prevents_joining(self, connect):
        host_ws = connect("host1")
        host_ws.send_json({"type": SignalType.JOIN.value})
        created = host_ws.receive_json()
        room_id = created["room_id"]

        host_ws.send_json({"type": SignalType.LOCK_ROOM.value})
        lock_msg = host_ws.receive_json()
        assert lock_msg["type"] == SignalType.ROOM_LOCKED.value

        p1_ws = connect("p1")
        p1_ws.send_json({"type": SignalType.JOIN.value, "room_id": room_id})
        response = p1_ws.receive_json()
        assert response["type"] == SignalType.ERROR.value
        assert response["reason"] == ErrorReason.LOCKED.value

    def test_unlock_allows_joining(self, connect):
        host_ws = connect("host1")
        host_ws.send_json({"type": SignalType.JOIN.value})
        created = host_ws.receive_json()
        room_id = created["room_id"]

        host_ws.send_json({"type": SignalType.LOCK_ROOM.value})
        host_ws.receive_json()

        host_ws.send_json({"type": SignalType.UNLOCK_ROOM.value})
        unlock_msg = host_ws.receive_json()
        assert unlock_msg["type"] == SignalType.ROOM_UNLOCKED.value

        p1_ws = connect("p1")
        p1_ws.send_json({"type": SignalType.JOIN.value, "room_id": room_id})
        response = p1_ws.receive_json()
        assert response["type"] == SignalType.EXISTING_PARTICIPANTS.value


@pytest.mark.integration
class TestDisconnect:
    def test_disconnect_notifies_remaining_peers(self, connect):
        host_ws = connect("host1")
        host_ws.send_json({"type": SignalType.JOIN.value})
        created = host_ws.receive_json()
        room_id = created["room_id"]

        p1_ws = connect("p1")
        p1_ws.send_json({"type": SignalType.JOIN.value, "room_id": room_id})
        p1_ws.receive_json()
        host_ws.receive_json()

        p1_ws.close()

        left_msg = host_ws.receive_json()
        assert left_msg["type"] == SignalType.PARTICIPANT_LEFT.value
        assert left_msg["participant_id"] == "p1"


@pytest.mark.integration
class TestNonTextFrames:
    def test_binary_frame_is_ignored_and_later_text_frames_still_process(self, connect):
        ws = connect("host1")

        ws.send_bytes(b"x")
        ws.send_json({"type": SignalType.JOIN.value})

        response = ws.receive_json()
        assert response["type"] == SignalType.ROOM_CREATED.value


@pytest.mark.integration
class TestChat:
    def test_chat_message_delivered_to_other_participants(self, connect):
        host_ws = connect("host1")
        host_ws.send_json({"type": SignalType.JOIN.value})
        created = host_ws.receive_json()
        room_id = created["room_id"]

        p1_ws = connect("p1")
        p1_ws.send_json({"type": SignalType.JOIN.value, "room_id": room_id})
        p1_ws.receive_json()
        host_ws.receive_json()

        host_ws.send_json({"type": SignalType.CHAT.value, "message": "hello everyone"})

        chat_msg = p1_ws.receive_json()
        assert chat_msg["type"] == SignalType.CHAT.value
        assert chat_msg["message"] == "hello everyone"
        assert chat_msg["from"] == "host1"
