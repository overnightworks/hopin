import pytest
import pytest_asyncio

from hopin.config.constants import SignalType
from tests.signaling.support.browsers import Link, assert_refused_as_invalid_message, deliver, seat_in_room, sent_frames

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def link(handler):
    link = Link(handler)
    yield link
    await link.close_all()


class TestChat:
    async def test_chat_broadcasts_to_other_participants(self, link, room_manager):
        _room, browsers = await seat_in_room(link, room_manager, "host1", "p1")

        await deliver(browsers["host1"], {"type": SignalType.CHAT.value, "message": "hello"})

        assert sent_frames(browsers["p1"]) == [
            {"type": SignalType.CHAT.value, "message": "hello", "from": browsers["host1"].peer_id}
        ]
        assert browsers["host1"].socket.sent == []

    async def test_chat_empty_message_is_ignored(self, link, room_manager):
        _room, browsers = await seat_in_room(link, room_manager, "host1", "p1")

        await deliver(browsers["host1"], {"type": SignalType.CHAT.value, "message": ""})

        assert browsers["p1"].socket.sent == []

    @pytest.mark.parametrize(
        "message",
        [
            pytest.param(5, id="int"),
            pytest.param(0, id="zero"),
            pytest.param(True, id="true"),
            pytest.param(False, id="false"),
            pytest.param(None, id="null"),
            pytest.param(["x"], id="list"),
            pytest.param([], id="empty_list"),
            pytest.param({"a": 1}, id="object"),
            pytest.param({}, id="empty_object"),
        ],
    )
    async def test_chat_with_a_non_string_message_is_answered_invalid_message(self, link, room_manager, message):
        _room, browsers = await seat_in_room(link, room_manager, "host1", "p1")

        await assert_refused_as_invalid_message(
            browsers["host1"], browsers["p1"], {"type": SignalType.CHAT.value, "message": message}
        )

    @pytest.mark.parametrize(
        "chat",
        [pytest.param({}, id="message_missing"), pytest.param({"message": ""}, id="message_empty")],
    )
    async def test_chat_without_a_message_is_dropped_without_an_error(self, link, room_manager, chat):
        _room, browsers = await seat_in_room(link, room_manager, "host1", "p1")

        await deliver(browsers["host1"], {"type": SignalType.CHAT.value, **chat})

        assert browsers["host1"].socket.sent == []
        assert browsers["p1"].socket.sent == []
