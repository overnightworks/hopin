import pytest

from openstream.config.constants import ROOM_ID_LENGTH
from openstream.errors import RoomFullError, RoomNotFoundError
from openstream.rooms.manager import RoomManager
from openstream.rooms.models import Room, Viewer


class TestViewer:
    def test_stores_viewer_id(self):
        viewer = Viewer(viewer_id="v1")
        assert viewer.viewer_id == "v1"


class TestRoom:
    def test_stores_room_id_and_streamer(self):
        room = Room(room_id="abc", streamer_id="s1")
        assert room.room_id == "abc"
        assert room.streamer_id == "s1"

    def test_starts_with_no_viewers(self):
        room = Room(room_id="abc", streamer_id="s1")
        assert room.viewer_count == 0

    def test_viewer_count_reflects_viewers(self):
        room = Room(room_id="abc", streamer_id="s1", viewers={"v1": Viewer("v1"), "v2": Viewer("v2")})
        assert room.viewer_count == 2


class TestRoomManager:
    @pytest.fixture
    def manager(self):
        return RoomManager(max_viewers_per_room=3)

    def test_create_room_returns_room(self, manager):
        room = manager.create_room("streamer1")
        assert isinstance(room, Room)
        assert room.streamer_id == "streamer1"

    def test_create_room_generates_valid_id(self, manager):
        room = manager.create_room("streamer1")
        assert len(room.room_id) == ROOM_ID_LENGTH
        assert room.room_id.isalnum()

    def test_create_room_increments_count(self, manager):
        assert manager.room_count == 0
        manager.create_room("s1")
        assert manager.room_count == 1
        manager.create_room("s2")
        assert manager.room_count == 2

    def test_get_room_returns_existing_room(self, manager):
        created = manager.create_room("s1")
        fetched = manager.get_room(created.room_id)
        assert fetched.room_id == created.room_id

    def test_get_room_raises_for_unknown_id(self, manager):
        with pytest.raises(RoomNotFoundError):
            manager.get_room("nonexistent")

    def test_has_room_returns_true_for_existing(self, manager):
        room = manager.create_room("s1")
        assert manager.has_room(room.room_id) is True

    def test_has_room_returns_false_for_missing(self, manager):
        assert manager.has_room("nonexistent") is False

    def test_add_viewer_adds_to_room(self, manager):
        room = manager.create_room("s1")
        updated = manager.add_viewer(room.room_id, "v1")
        assert updated.viewer_count == 1
        assert "v1" in updated.viewers

    def test_add_viewer_raises_when_full(self, manager):
        room = manager.create_room("s1")
        manager.add_viewer(room.room_id, "v1")
        manager.add_viewer(room.room_id, "v2")
        manager.add_viewer(room.room_id, "v3")
        with pytest.raises(RoomFullError):
            manager.add_viewer(room.room_id, "v4")

    def test_add_viewer_raises_for_unknown_room(self, manager):
        with pytest.raises(RoomNotFoundError):
            manager.add_viewer("nonexistent", "v1")

    def test_remove_viewer_removes_from_room(self, manager):
        room = manager.create_room("s1")
        manager.add_viewer(room.room_id, "v1")
        updated = manager.remove_viewer(room.room_id, "v1")
        assert updated.viewer_count == 0

    def test_remove_viewer_ignores_unknown_viewer(self, manager):
        room = manager.create_room("s1")
        updated = manager.remove_viewer(room.room_id, "unknown")
        assert updated.viewer_count == 0

    def test_remove_room_deletes_it(self, manager):
        room = manager.create_room("s1")
        manager.remove_room(room.room_id)
        assert manager.has_room(room.room_id) is False

    def test_remove_room_ignores_unknown(self, manager):
        manager.remove_room("nonexistent")
        assert manager.room_count == 0

    def test_unique_room_ids(self, manager):
        rooms = [manager.create_room(f"s{i}") for i in range(20)]
        ids = {r.room_id for r in rooms}
        assert len(ids) == 20
