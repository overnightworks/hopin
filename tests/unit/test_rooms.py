import pytest

from hopin.config.constants import ROOM_ID_LENGTH
from hopin.errors import NotHostError, RoomFullError, RoomLockedError, RoomNotFoundError
from hopin.rooms.manager import RoomManager
from hopin.rooms.models import Account, Participant, Room

SEAT_LIMIT = 3


def account_named(name):
    return Account(id=name, name=name)


ACCOUNT_P1 = account_named("p1")
ACCOUNT_P3 = account_named("p3")


def participant_named(name):
    return Participant(participant_id=name, account=account_named(name))


def room_with(*names, host="h1"):
    return Room(
        room_id="abc",
        host=account_named(host),
        participants={name: participant_named(name) for name in names},
    )


class TestParticipant:
    def test_stores_participant_id_and_account(self):
        participant = participant_named("p1")
        assert participant.participant_id == "p1"
        assert participant.account == account_named("p1")


class TestRoom:
    def test_stores_room_id_and_host(self):
        room = room_with()
        assert room.room_id == "abc"
        assert room.host == account_named("h1")

    def test_starts_with_no_participants(self):
        assert room_with().participant_count == 0

    def test_participant_count_reflects_participants(self):
        assert room_with("p1", "p2").participant_count == 2

    def test_participant_ids_returns_keys(self):
        assert sorted(room_with("p1", "p2").participant_ids) == ["p1", "p2"]

    def test_default_not_locked(self):
        assert room_with().locked is False

    def test_is_hosted_by_compares_the_account_id_not_the_name(self):
        room = room_with()
        assert room.is_hosted_by(Account(id="h1", name="renamed"))
        assert not room.is_hosted_by(account_named("p1"))

    @pytest.mark.parametrize(
        ("present", "expected"),
        [(("p1",), False), (("p1", "h1"), True), ((), False)],
        ids=["guest_only", "host_connected", "empty"],
    )
    def test_host_present_when_some_participant_is_the_host_account(self, present, expected):
        assert room_with(*present).host_present is expected

    @pytest.mark.parametrize(
        ("present", "joiner", "has_seat"),
        [
            (("h1", "p1"), "p2", True),
            (("h1", "p1", "p2"), "p3", False),
            (("p1", "p2"), "p3", False),
            (("p1", "p2"), "h1", True),
            (("h1", "p1", "p2"), "h1", False),
        ],
        ids=["free_seat", "full", "away_host_seat_reserved", "away_host_takes_own_seat", "present_host_full"],
    )
    def test_has_free_seat_for_counts_the_away_hosts_seat_as_taken(self, present, joiner, has_seat):
        assert room_with(*present).has_free_seat_for(account_named(joiner), SEAT_LIMIT) is has_seat


class TestRoomManager:
    @pytest.fixture
    def manager(self):
        return RoomManager(max_participants_per_room=3)

    def test_create_room_returns_room(self, manager):
        room = manager.create_room("host1", account_named("host1"))
        assert isinstance(room, Room)
        assert room.host == account_named("host1")

    def test_create_room_adds_host_as_participant(self, manager):
        room = manager.create_room("host1", account_named("host1"))
        assert "host1" in room.participants
        assert room.participant_count == 1

    def test_create_room_generates_valid_id(self, manager):
        room = manager.create_room("host1", account_named("host1"))
        assert len(room.room_id) == ROOM_ID_LENGTH
        assert room.room_id.isalnum()

    def test_create_room_increments_count(self, manager):
        assert manager.room_count == 0
        manager.create_room("h1", account_named("h1"))
        assert manager.room_count == 1
        manager.create_room("h2", account_named("h2"))
        assert manager.room_count == 2

    def test_get_room_returns_existing_room(self, manager):
        created = manager.create_room("h1", account_named("h1"))
        fetched = manager.get_room(created.room_id)
        assert fetched.room_id == created.room_id

    def test_get_room_raises_for_unknown_id(self, manager):
        with pytest.raises(RoomNotFoundError):
            manager.get_room("nonexistent")

    def test_has_room_returns_true_for_existing(self, manager):
        room = manager.create_room("h1", account_named("h1"))
        assert manager.has_room(room.room_id) is True

    def test_has_room_returns_false_for_missing(self, manager):
        assert manager.has_room("nonexistent") is False

    def test_add_participant_adds_to_room(self, manager):
        room = manager.create_room("h1", account_named("h1"))
        updated = manager.add_participant(room.room_id, "p1", account_named("p1"))
        assert updated.participant_count == 2
        assert "p1" in updated.participants

    def test_add_participant_raises_when_locked(self, manager):
        room = manager.create_room("h1", account_named("h1"))
        room.locked = True
        room_id = room.room_id
        with pytest.raises(RoomLockedError):
            manager.add_participant(room_id, "p1", ACCOUNT_P1)

    def test_add_participant_locked_and_full_raises_locked(self, manager):
        room = manager.create_room("h1", account_named("h1"))
        manager.add_participant(room.room_id, "p1", account_named("p1"))
        manager.add_participant(room.room_id, "p2", account_named("p2"))
        room.locked = True
        room_id = room.room_id
        with pytest.raises(RoomLockedError):
            manager.add_participant(room_id, "p3", ACCOUNT_P3)

    def test_add_participant_raises_when_full(self, manager):
        room = manager.create_room("h1", account_named("h1"))
        manager.add_participant(room.room_id, "p1", account_named("p1"))
        manager.add_participant(room.room_id, "p2", account_named("p2"))
        room_id = room.room_id
        with pytest.raises(RoomFullError) as refusal:
            manager.add_participant(room_id, "p3", ACCOUNT_P3)
        assert refusal.value.capacity == 3

    def test_add_participant_raises_for_unknown_room(self, manager):
        with pytest.raises(RoomNotFoundError):
            manager.add_participant("nonexistent", "p1", ACCOUNT_P1)

    def test_set_room_locked_by_host(self, manager):
        room = manager.create_room("h1", account_named("h1"))
        updated = manager.set_room_locked(room.room_id, account_named("h1"), locked=True)
        assert updated.locked is True

    def test_set_room_unlocked_by_host(self, manager):
        room = manager.create_room("h1", account_named("h1"))
        manager.set_room_locked(room.room_id, account_named("h1"), locked=True)
        updated = manager.set_room_locked(room.room_id, account_named("h1"), locked=False)
        assert updated.locked is False

    def test_add_participant_host_account_passes_the_lock(self, manager):
        room = manager.create_room("h1", account_named("h1"))
        room.locked = True
        updated = manager.add_participant(room.room_id, "h1-second-device", account_named("h1"))
        assert "h1-second-device" in updated.participants

    def test_add_participant_stranger_cannot_take_the_away_hosts_seat(self, manager):
        room = manager.create_room("h1", account_named("h1"))
        manager.add_participant(room.room_id, "p1", account_named("p1"))
        manager.remove_participant(room.room_id, "h1")
        manager.add_participant(room.room_id, "p2", account_named("p2"))
        room_id = room.room_id
        with pytest.raises(RoomFullError):
            manager.add_participant(room_id, "p3", ACCOUNT_P3)

    def test_add_participant_returning_host_takes_the_reserved_seat(self, manager):
        room = manager.create_room("h1", account_named("h1"))
        manager.add_participant(room.room_id, "p1", account_named("p1"))
        manager.remove_participant(room.room_id, "h1")
        manager.add_participant(room.room_id, "p2", account_named("p2"))
        updated = manager.add_participant(room.room_id, "h1-back", account_named("h1"))
        assert updated.host_present

    def test_set_room_locked_by_non_host_raises(self, manager):
        room = manager.create_room("h1", account_named("h1"))
        manager.add_participant(room.room_id, "p1", account_named("p1"))
        room_id = room.room_id
        with pytest.raises(NotHostError):
            manager.set_room_locked(room_id, ACCOUNT_P1, locked=True)

    def test_remove_participant_removes_from_room(self, manager):
        room = manager.create_room("h1", account_named("h1"))
        manager.add_participant(room.room_id, "p1", account_named("p1"))
        updated = manager.remove_participant(room.room_id, "p1")
        assert updated.participant_count == 1
        assert "p1" not in updated.participants

    def test_remove_participant_ignores_unknown(self, manager):
        room = manager.create_room("h1", account_named("h1"))
        updated = manager.remove_participant(room.room_id, "unknown")
        assert updated.participant_count == 1

    def test_remove_room_deletes_it(self, manager):
        room = manager.create_room("h1", account_named("h1"))
        manager.remove_room(room.room_id)
        assert manager.has_room(room.room_id) is False

    def test_remove_room_ignores_unknown(self, manager):
        manager.remove_room("nonexistent")
        assert manager.room_count == 0

    def test_unique_room_ids(self, manager):
        rooms = [manager.create_room(f"h{i}", account_named(f"h{i}")) for i in range(20)]
        ids = {r.room_id for r in rooms}
        assert len(ids) == 20

    def test_retries_generation_on_id_collision(self, manager, monkeypatch):
        taken = manager.create_room("h1", account_named("h1")).room_id
        fresh_id = "z" * ROOM_ID_LENGTH
        attempted_ids = iter([*taken, *fresh_id])
        monkeypatch.setattr("hopin.rooms.manager.secrets.choice", lambda _alphabet: next(attempted_ids))

        room = manager.create_room("h2", account_named("h2"))

        assert room.room_id == fresh_id
