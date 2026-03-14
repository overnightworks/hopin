import pytest

from openstream.config.constants import ROOM_ID_LENGTH
from openstream.errors import RoomFullError, RoomNotFoundError
from openstream.rooms.manager import RoomManager
from openstream.rooms.models import Participant, Room


class TestParticipant:
    def test_stores_participant_id(self):
        participant = Participant(participant_id="p1")
        assert participant.participant_id == "p1"


class TestRoom:
    def test_stores_room_id_and_host(self):
        room = Room(room_id="abc", host_id="h1")
        assert room.room_id == "abc"
        assert room.host_id == "h1"

    def test_starts_with_no_participants(self):
        room = Room(room_id="abc", host_id="h1")
        assert room.participant_count == 0

    def test_participant_count_reflects_participants(self):
        room = Room(
            room_id="abc",
            host_id="h1",
            participants={"p1": Participant("p1"), "p2": Participant("p2")},
        )
        assert room.participant_count == 2

    def test_participant_ids_returns_keys(self):
        room = Room(
            room_id="abc",
            host_id="h1",
            participants={"p1": Participant("p1"), "p2": Participant("p2")},
        )
        assert sorted(room.participant_ids) == ["p1", "p2"]

    def test_is_empty_when_no_participants(self):
        room = Room(room_id="abc", host_id="h1")
        assert room.is_empty is True

    def test_is_not_empty_with_participants(self):
        room = Room(
            room_id="abc",
            host_id="h1",
            participants={"p1": Participant("p1")},
        )
        assert room.is_empty is False


class TestRoomManager:
    @pytest.fixture
    def manager(self):
        return RoomManager(max_participants_per_room=3)

    def test_create_room_returns_room(self, manager):
        room = manager.create_room("host1")
        assert isinstance(room, Room)
        assert room.host_id == "host1"

    def test_create_room_adds_host_as_participant(self, manager):
        room = manager.create_room("host1")
        assert "host1" in room.participants
        assert room.participant_count == 1

    def test_create_room_generates_valid_id(self, manager):
        room = manager.create_room("host1")
        assert len(room.room_id) == ROOM_ID_LENGTH
        assert room.room_id.isalnum()

    def test_create_room_increments_count(self, manager):
        assert manager.room_count == 0
        manager.create_room("h1")
        assert manager.room_count == 1
        manager.create_room("h2")
        assert manager.room_count == 2

    def test_get_room_returns_existing_room(self, manager):
        created = manager.create_room("h1")
        fetched = manager.get_room(created.room_id)
        assert fetched.room_id == created.room_id

    def test_get_room_raises_for_unknown_id(self, manager):
        with pytest.raises(RoomNotFoundError):
            manager.get_room("nonexistent")

    def test_has_room_returns_true_for_existing(self, manager):
        room = manager.create_room("h1")
        assert manager.has_room(room.room_id) is True

    def test_has_room_returns_false_for_missing(self, manager):
        assert manager.has_room("nonexistent") is False

    def test_add_participant_adds_to_room(self, manager):
        room = manager.create_room("h1")
        updated = manager.add_participant(room.room_id, "p1")
        assert updated.participant_count == 2
        assert "p1" in updated.participants

    def test_add_participant_raises_when_full(self, manager):
        room = manager.create_room("h1")
        manager.add_participant(room.room_id, "p1")
        manager.add_participant(room.room_id, "p2")
        with pytest.raises(RoomFullError):
            manager.add_participant(room.room_id, "p3")

    def test_add_participant_raises_for_unknown_room(self, manager):
        with pytest.raises(RoomNotFoundError):
            manager.add_participant("nonexistent", "p1")

    def test_remove_participant_removes_from_room(self, manager):
        room = manager.create_room("h1")
        manager.add_participant(room.room_id, "p1")
        updated = manager.remove_participant(room.room_id, "p1")
        assert updated.participant_count == 1
        assert "p1" not in updated.participants

    def test_remove_participant_ignores_unknown(self, manager):
        room = manager.create_room("h1")
        updated = manager.remove_participant(room.room_id, "unknown")
        assert updated.participant_count == 1

    def test_remove_room_deletes_it(self, manager):
        room = manager.create_room("h1")
        manager.remove_room(room.room_id)
        assert manager.has_room(room.room_id) is False

    def test_remove_room_ignores_unknown(self, manager):
        manager.remove_room("nonexistent")
        assert manager.room_count == 0

    def test_unique_room_ids(self, manager):
        rooms = [manager.create_room(f"h{i}") for i in range(20)]
        ids = {r.room_id for r in rooms}
        assert len(ids) == 20
