import json

import pytest
from aiohttp import WSMsgType

from hopin.app import create_app
from hopin.config.constants import SignalType
from hopin.config.settings import ServerSettings


@pytest.fixture
def app():
    settings = ServerSettings(max_participants_per_room=3)
    return create_app(settings)


async def connect_ws(client, connection_id):
    return await client.ws_connect(f"/ws?id={connection_id}")


async def send(ws, data):
    await ws.send_str(json.dumps(data))


async def receive(ws):
    msg = await ws.receive()
    return json.loads(msg.data)


@pytest.mark.integration
class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_without_id_closes_the_socket(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        ws = await client.ws_connect("/ws")
        msg = await ws.receive()
        assert msg.type == WSMsgType.CLOSE


@pytest.mark.integration
class TestRoomCreation:
    @pytest.mark.asyncio
    async def test_create_room_returns_room_id(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        ws = await connect_ws(client, "host1")
        await send(ws, {"type": SignalType.JOIN.value})
        response = await receive(ws)
        assert response["type"] == SignalType.ROOM_CREATED.value
        assert "room_id" in response
        assert response["is_host"] is True
        await ws.close()

    @pytest.mark.asyncio
    async def test_create_room_with_password(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        ws = await connect_ws(client, "host1")
        await send(ws, {"type": SignalType.JOIN.value, "password": "secret"})
        response = await receive(ws)
        assert response["type"] == SignalType.ROOM_CREATED.value
        await ws.close()


@pytest.mark.integration
class TestJoining:
    @pytest.mark.asyncio
    async def test_join_existing_room(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        host_ws = await connect_ws(client, "host1")
        await send(host_ws, {"type": SignalType.JOIN.value})
        created = await receive(host_ws)
        room_id = created["room_id"]

        p1_ws = await connect_ws(client, "p1")
        await send(p1_ws, {"type": SignalType.JOIN.value, "room_id": room_id})

        p1_response = await receive(p1_ws)
        assert p1_response["type"] == SignalType.EXISTING_PARTICIPANTS.value
        assert "host1" in p1_response["participants"]
        assert p1_response["is_host"] is False

        host_notification = await receive(host_ws)
        assert host_notification["type"] == SignalType.PARTICIPANT_JOINED.value
        assert host_notification["participant_id"] == "p1"

        await host_ws.close()
        await p1_ws.close()

    @pytest.mark.asyncio
    async def test_join_nonexistent_room_sends_error(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        ws = await connect_ws(client, "p1")
        await send(ws, {"type": SignalType.JOIN.value, "room_id": "nonexistent"})
        response = await receive(ws)
        assert response["type"] == SignalType.ERROR.value
        await ws.close()

    @pytest.mark.asyncio
    async def test_join_with_correct_password(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        host_ws = await connect_ws(client, "host1")
        await send(host_ws, {"type": SignalType.JOIN.value, "password": "secret"})
        created = await receive(host_ws)
        room_id = created["room_id"]

        p1_ws = await connect_ws(client, "p1")
        await send(p1_ws, {"type": SignalType.JOIN.value, "room_id": room_id, "password": "secret"})
        response = await receive(p1_ws)
        assert response["type"] == SignalType.EXISTING_PARTICIPANTS.value

        await host_ws.close()
        await p1_ws.close()

    @pytest.mark.asyncio
    async def test_join_with_wrong_password_sends_error(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        host_ws = await connect_ws(client, "host1")
        await send(host_ws, {"type": SignalType.JOIN.value, "password": "secret"})
        created = await receive(host_ws)
        room_id = created["room_id"]

        p1_ws = await connect_ws(client, "p1")
        await send(p1_ws, {"type": SignalType.JOIN.value, "room_id": room_id, "password": "wrong"})
        response = await receive(p1_ws)
        assert response["type"] == SignalType.ERROR.value
        assert "Wrong password" in response["message"]

        await host_ws.close()
        await p1_ws.close()

    @pytest.mark.asyncio
    async def test_full_room_sends_error(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        host_ws = await connect_ws(client, "host1")
        await send(host_ws, {"type": SignalType.JOIN.value})
        created = await receive(host_ws)
        room_id = created["room_id"]

        p1_ws = await connect_ws(client, "p1")
        await send(p1_ws, {"type": SignalType.JOIN.value, "room_id": room_id})
        await receive(p1_ws)
        await receive(host_ws)

        p2_ws = await connect_ws(client, "p2")
        await send(p2_ws, {"type": SignalType.JOIN.value, "room_id": room_id})
        await receive(p2_ws)
        await receive(host_ws)
        await receive(p1_ws)

        p3_ws = await connect_ws(client, "p3")
        await send(p3_ws, {"type": SignalType.JOIN.value, "room_id": room_id})
        response = await receive(p3_ws)
        assert response["type"] == SignalType.ERROR.value

        await host_ws.close()
        await p1_ws.close()
        await p2_ws.close()
        await p3_ws.close()


@pytest.mark.integration
class TestRelay:
    @pytest.mark.asyncio
    async def test_offer_relayed_between_peers(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        host_ws = await connect_ws(client, "host1")
        await send(host_ws, {"type": SignalType.JOIN.value})
        created = await receive(host_ws)
        room_id = created["room_id"]

        p1_ws = await connect_ws(client, "p1")
        await send(p1_ws, {"type": SignalType.JOIN.value, "room_id": room_id})
        await receive(p1_ws)
        await receive(host_ws)

        await send(
            p1_ws,
            {
                "type": SignalType.OFFER.value,
                "target": "host1",
                "sdp": "test_sdp_offer",
            },
        )

        relayed = await receive(host_ws)
        assert relayed["type"] == SignalType.OFFER.value
        assert relayed["sdp"] == "test_sdp_offer"
        assert relayed["from"] == "p1"

        await host_ws.close()
        await p1_ws.close()


@pytest.mark.integration
class TestLockUnlock:
    @pytest.mark.asyncio
    async def test_lock_prevents_joining(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        host_ws = await connect_ws(client, "host1")
        await send(host_ws, {"type": SignalType.JOIN.value})
        created = await receive(host_ws)
        room_id = created["room_id"]

        await send(host_ws, {"type": SignalType.LOCK_ROOM.value})
        lock_msg = await receive(host_ws)
        assert lock_msg["type"] == SignalType.ROOM_LOCKED.value

        p1_ws = await connect_ws(client, "p1")
        await send(p1_ws, {"type": SignalType.JOIN.value, "room_id": room_id})
        response = await receive(p1_ws)
        assert response["type"] == SignalType.ERROR.value
        assert "locked" in response["message"].lower()

        await host_ws.close()
        await p1_ws.close()

    @pytest.mark.asyncio
    async def test_unlock_allows_joining(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        host_ws = await connect_ws(client, "host1")
        await send(host_ws, {"type": SignalType.JOIN.value})
        created = await receive(host_ws)
        room_id = created["room_id"]

        await send(host_ws, {"type": SignalType.LOCK_ROOM.value})
        await receive(host_ws)

        await send(host_ws, {"type": SignalType.UNLOCK_ROOM.value})
        unlock_msg = await receive(host_ws)
        assert unlock_msg["type"] == SignalType.ROOM_UNLOCKED.value

        p1_ws = await connect_ws(client, "p1")
        await send(p1_ws, {"type": SignalType.JOIN.value, "room_id": room_id})
        response = await receive(p1_ws)
        assert response["type"] == SignalType.EXISTING_PARTICIPANTS.value

        await host_ws.close()
        await p1_ws.close()


@pytest.mark.integration
class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_notifies_remaining_peers(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        host_ws = await connect_ws(client, "host1")
        await send(host_ws, {"type": SignalType.JOIN.value})
        created = await receive(host_ws)
        room_id = created["room_id"]

        p1_ws = await connect_ws(client, "p1")
        await send(p1_ws, {"type": SignalType.JOIN.value, "room_id": room_id})
        await receive(p1_ws)
        await receive(host_ws)

        await p1_ws.close()

        left_msg = await receive(host_ws)
        assert left_msg["type"] == SignalType.PARTICIPANT_LEFT.value
        assert left_msg["participant_id"] == "p1"

        await host_ws.close()


@pytest.mark.integration
class TestNonTextFrames:
    @pytest.mark.asyncio
    async def test_binary_frame_is_ignored_and_later_text_frames_still_process(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        ws = await connect_ws(client, "host1")

        await ws.send_bytes(b"x")
        await send(ws, {"type": SignalType.JOIN.value})

        response = await receive(ws)
        assert response["type"] == SignalType.ROOM_CREATED.value

        await ws.close()


@pytest.mark.integration
class TestChat:
    @pytest.mark.asyncio
    async def test_chat_message_delivered_to_other_participants(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        host_ws = await connect_ws(client, "host1")
        await send(host_ws, {"type": SignalType.JOIN.value})
        created = await receive(host_ws)
        room_id = created["room_id"]

        p1_ws = await connect_ws(client, "p1")
        await send(p1_ws, {"type": SignalType.JOIN.value, "room_id": room_id})
        await receive(p1_ws)
        await receive(host_ws)

        await send(host_ws, {"type": SignalType.CHAT.value, "message": "hello everyone"})

        chat_msg = await receive(p1_ws)
        assert chat_msg["type"] == SignalType.CHAT.value
        assert chat_msg["message"] == "hello everyone"
        assert chat_msg["from"] == "host1"

        await host_ws.close()
        await p1_ws.close()
