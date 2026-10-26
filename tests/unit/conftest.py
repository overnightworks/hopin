import pytest

from hopin.rooms.manager import RoomManager
from hopin.signaling.handler import SignalingHandler
from tests.support.signaling_fakes import ROOM_LIMIT, ManualScheduler, SocketAccountResolver


@pytest.fixture
def room_manager():
    return RoomManager(max_participants_per_room=ROOM_LIMIT)


@pytest.fixture
def scheduler():
    return ManualScheduler()


@pytest.fixture
def handler(room_manager, scheduler):
    return SignalingHandler(room_manager, SocketAccountResolver(), scheduler)
