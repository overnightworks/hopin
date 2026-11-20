"""What an agent's open stream is told, in order, when its standing or a window changes."""

import pytest
from agents.mcp_server import PlaceStoreError
from agents.mcp_server.place_store import ToolsChange
from agents.mcp_server.redis_place_store import RedisPlaceStore
from agents.member import STANDING_URI

from hopin.agent.mcp import PLACE_SIGNAL_FAILED_EVENT
from hopin.config.constants import SignalType
from tests.support.dialed_call import LISTEN_TIMEOUT_SECONDS, RESOURCE_UPDATED, TOOLS_LIST_CHANGED
from tests.support.log_lines import captured_lines

pytestmark = pytest.mark.integration


def says(seat, message):
    seat.send({"type": SignalType.CHAT.value, "message": message})


def told(stream):
    return stream.arrivals(TOOLS_LIST_CHANGED, RESOURCE_UPDATED)


class TestTheTwoNoticesOfAChange:
    def test_a_window_change_wakes_without_a_tools_notice(self, scene):
        scene.invite()
        scene.accept(scene.standings()[0])
        with scene.call.open_listen(scene.token, [STANDING_URI], tools=True) as stream:
            says(scene.owner, "hello")
            assert stream.wait_for(RESOURCE_UPDATED, LISTEN_TIMEOUT_SECONDS)

            assert told(stream) == [RESOURCE_UPDATED]

    def test_a_tools_notice_the_store_refuses_does_not_hold_back_the_wake(self, scene, monkeypatch):
        publish = RedisPlaceStore.publish

        async def refusing_tools(store, signal):
            if isinstance(signal, ToolsChange):
                raise PlaceStoreError
            await publish(store, signal)

        monkeypatch.setattr(RedisPlaceStore, "publish", refusing_tools)
        with scene.call.open_listen(scene.token, [STANDING_URI], tools=True) as stream, captured_lines() as lines:
            scene.invite()

            assert stream.wait_for(RESOURCE_UPDATED, LISTEN_TIMEOUT_SECONDS)
            assert lines.wait_for_event(PLACE_SIGNAL_FAILED_EVENT, LISTEN_TIMEOUT_SECONDS)

        assert told(stream) == [RESOURCE_UPDATED]
        assert [line["event"] for line in lines].count(PLACE_SIGNAL_FAILED_EVENT) == 1
