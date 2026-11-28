import pytest

from hopin.errors import NotHostError, RoomFullError, RoomLockedError
from hopin.rooms.models import Account, AgentPlace, Participant, Room

SEAT_LIMIT = 3


def account_named(name):
    return Account(id=name, name=name)


ACCOUNT_P1 = account_named("p1")
ACCOUNT_P3 = account_named("p3")


def participant_named(name):
    return Participant(participant_id=name, account=account_named(name))


def room_with(*names, host="h1", agents=0, locked=False):
    return Room(
        room_id="abc",
        host=account_named(host),
        locked=locked,
        places=tuple(AgentPlace(f"agent{number}", "generation") for number in range(agents)),
        participants=tuple(participant_named(name) for name in names),
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
        """CALL-01: Is hosted by compares the account id not the name."""
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

    @pytest.mark.parametrize(
        ("present", "agents", "has_place"),
        [
            (("h1",), 1, True),
            (("h1", "p1"), 1, False),
            (("h1",), 2, False),
            (("p1",), 1, False),
        ],
        ids=["agent_beside_host", "agent_fills_last_place", "agents_fill_call", "away_host_place_reserved"],
    )
    def test_has_free_place_for_agent_counts_people_and_joined_agents_together(self, present, agents, has_place):
        room = room_with(*present, agents=agents)
        assert room.has_free_place_for_agent(SEAT_LIMIT) is has_place

    def test_a_person_finds_no_seat_when_joined_agents_fill_the_places(self):
        room = room_with("h1", agents=2)
        assert not room.has_free_seat_for(account_named("p1"), SEAT_LIMIT)


class TestAdmission:
    def test_a_locked_room_refuses_a_stranger(self):
        with pytest.raises(RoomLockedError):
            room_with("h1", locked=True).check_admission(ACCOUNT_P1, SEAT_LIMIT)

    def test_a_locked_and_full_room_refuses_as_locked(self):
        with pytest.raises(RoomLockedError):
            room_with("h1", "p1", "p2", locked=True).check_admission(ACCOUNT_P3, SEAT_LIMIT)

    def test_a_full_room_refuses_with_its_capacity(self):
        with pytest.raises(RoomFullError) as refusal:
            room_with("h1", "p1", "p2").check_admission(ACCOUNT_P3, SEAT_LIMIT)
        assert refusal.value.capacity == SEAT_LIMIT

    def test_the_host_account_passes_the_lock(self):
        room_with("h1", locked=True).check_admission(account_named("h1"), SEAT_LIMIT)

    def test_add_participant_stranger_cannot_take_the_away_hosts_seat(self):
        """CALL-07: Add participant stranger cannot take the away hosts seat."""
        room = room_with("p1", "p2")

        with pytest.raises(RoomFullError):
            room.check_admission(ACCOUNT_P3, SEAT_LIMIT)

    def test_the_returning_host_takes_the_reserved_seat(self):
        room_with("p1", "p2").check_admission(account_named("h1"), SEAT_LIMIT)

    def test_an_agent_takes_a_place_a_person_could_have_taken(self):
        room = room_with("h1", "p1", agents=0).with_agent(AgentPlace("agent0", "generation"), SEAT_LIMIT)

        with pytest.raises(RoomFullError):
            room.check_admission(account_named("p2"), SEAT_LIMIT)

    def test_an_agent_is_refused_when_people_fill_the_places(self):
        with pytest.raises(RoomFullError) as refusal:
            room_with("h1", "p1", "p2").with_agent(AgentPlace("agent0", "generation"), SEAT_LIMIT)
        assert refusal.value.capacity == SEAT_LIMIT

    def test_a_released_agent_frees_its_place(self):
        room = room_with("h1", agents=2).without_agent("agent0")

        assert room.has_free_place_for_agent(SEAT_LIMIT)

    def test_only_the_host_account_may_act_as_host(self):
        room = room_with("h1")

        room.check_hosted_by(account_named("h1"))
        with pytest.raises(NotHostError):
            room.check_hosted_by(ACCOUNT_P1)
