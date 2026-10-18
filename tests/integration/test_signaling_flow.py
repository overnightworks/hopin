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
    with TestClient(create_app(settings)) as shared_portal_client:
        yield shared_portal_client


@pytest.fixture
def connect(client):
    with ExitStack() as sessions:
        yield lambda account: sessions.enter_context(client.websocket_connect(f"/ws?id={account}"))


def open_room(connect, account):
    ws = connect(account)
    ws.send_json({"type": SignalType.JOIN.value})
    return ws, ws.receive_json()["room_id"]


def join_room(connect, account, room_id):
    ws = connect(account)
    ws.send_json({"type": SignalType.JOIN.value, "room_id": room_id})
    return ws, ws.receive_json()


@pytest.mark.integration
class TestConnect:
    @pytest.mark.parametrize("path", ["/ws", "/ws?id="], ids=["missing_id", "empty_id"])
    def test_connect_without_account_is_refused_at_the_handshake(self, client, path):
        with pytest.raises(WebSocketDisconnect) as refused, client.websocket_connect(path):
            pass
        assert refused.value.code == 1008


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
        [host_peer_id] = p1_response["participants"]
        assert host_peer_id not in {"host1", "p1"}
        assert p1_response["is_host"] is False

        host_notification = host_ws.receive_json()
        assert host_notification["type"] == SignalType.PARTICIPANT_JOINED.value
        assert host_notification["participant_id"] not in {"host1", "p1", host_peer_id}

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

        p1_ws, existing = join_room(connect, "p1", room_id)
        [host_peer_id] = existing["participants"]
        p1_peer_id = host_ws.receive_json()["participant_id"]

        p1_ws.send_json(
            {
                "type": SignalType.OFFER.value,
                "target": host_peer_id,
                "sdp": "test_sdp_offer",
            },
        )

        relayed = host_ws.receive_json()
        assert relayed["type"] == SignalType.OFFER.value
        assert relayed["sdp"] == "test_sdp_offer"
        assert relayed["from"] == p1_peer_id


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

        p1_ws, _existing = join_room(connect, "p1", room_id)
        p1_peer_id = host_ws.receive_json()["participant_id"]

        p1_ws.close()

        left_msg = host_ws.receive_json()
        assert left_msg["type"] == SignalType.PARTICIPANT_LEFT.value
        assert left_msg["participant_id"] == p1_peer_id


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

        p1_ws, existing = join_room(connect, "p1", room_id)
        [host_peer_id] = existing["participants"]
        host_ws.receive_json()

        host_ws.send_json({"type": SignalType.CHAT.value, "message": "hello everyone"})

        chat_msg = p1_ws.receive_json()
        assert chat_msg["type"] == SignalType.CHAT.value
        assert chat_msg["message"] == "hello everyone"
        assert chat_msg["from"] == host_peer_id


@pytest.mark.integration
class TestPeersAreServerMinted:
    def test_two_sockets_of_one_account_join_as_two_peers_that_reach_each_other(self, connect):
        first_ws, room_id = open_room(connect, "alice")
        second_ws, existing = join_room(connect, "alice", room_id)
        [first_peer_id] = existing["participants"]
        second_peer_id = first_ws.receive_json()["participant_id"]
        assert first_peer_id != second_peer_id

        second_ws.send_json({"type": SignalType.OFFER.value, "target": first_peer_id, "sdp": "from_second"})
        first_ws.send_json({"type": SignalType.OFFER.value, "target": second_peer_id, "sdp": "from_first"})

        to_first = first_ws.receive_json()
        to_second = second_ws.receive_json()
        assert (to_first["sdp"], to_first["from"]) == ("from_second", second_peer_id)
        assert (to_second["sdp"], to_second["from"]) == ("from_first", first_peer_id)

    def test_socket_naming_another_peers_minted_id_does_not_take_over_its_entry(self, connect):
        host_ws, room_id = open_room(connect, "host1")
        guest_ws, existing = join_room(connect, "guest", room_id)
        [host_peer_id] = existing["participants"]
        guest_peer_id = host_ws.receive_json()["participant_id"]

        _impostor_ws, impostor_existing = join_room(connect, host_peer_id, room_id)
        assert set(impostor_existing["participants"]) == {host_peer_id, guest_peer_id}
        impostor_peer_id = host_ws.receive_json()["participant_id"]
        guest_ws.receive_json()
        assert impostor_peer_id != host_peer_id

        guest_ws.send_json({"type": SignalType.OFFER.value, "target": host_peer_id, "sdp": "for_host"})

        relayed = host_ws.receive_json()
        assert (relayed["sdp"], relayed["from"]) == ("for_host", guest_peer_id)

    def test_relay_to_an_unknown_peer_leaves_the_sender_connected(self, connect):
        ws, _room_id = open_room(connect, "host1")

        ws.send_json({"type": SignalType.OFFER.value, "target": "nobody", "sdp": "lost"})
        ws.send_json({"type": SignalType.OFFER.value, "target": []})

        response = ws.receive_json()
        assert response["reason"] == ErrorReason.INVALID_MESSAGE.value
