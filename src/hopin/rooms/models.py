from dataclasses import dataclass, field


@dataclass(frozen=True)
class Account:
    id: str
    name: str


@dataclass
class Participant:
    participant_id: str
    account: Account


@dataclass
class Room:
    room_id: str
    host: Account
    locked: bool = False
    participants: dict[str, Participant] = field(default_factory=dict)
    agent_ids: set[str] = field(default_factory=set)

    @property
    def participant_count(self) -> int:
        return len(self.participants)

    @property
    def participant_ids(self) -> list[str]:
        return list(self.participants.keys())

    @property
    def host_present(self) -> bool:
        return any(self.is_hosted_by(participant.account) for participant in self.participants.values())

    def is_hosted_by(self, account: Account) -> bool:
        return account.id == self.host.id

    def has_free_seat_for(self, account: Account, seat_limit: int) -> bool:
        return self._has_free_place(host_place_reserved=not self.is_hosted_by(account), place_limit=seat_limit)

    def has_free_place_for_agent(self, place_limit: int) -> bool:
        return self._has_free_place(host_place_reserved=True, place_limit=place_limit)

    def _has_free_place(self, *, host_place_reserved: bool, place_limit: int) -> bool:
        """People and joined agents take the same places; an away host's place stays free for the host."""
        reserved = host_place_reserved and not self.host_present
        return self.participant_count + len(self.agent_ids) + int(reserved) < place_limit
