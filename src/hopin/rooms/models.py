from dataclasses import dataclass, field


@dataclass(frozen=True)
class Account:
    id: str
    name: str


@dataclass
class Participant:
    participant_id: str
    account: Account


@dataclass(frozen=True)
class HostGrace:
    """The wait for a host who left: which of the host's seats left and when the room ends if no host is back."""

    host_peer_id: str
    deadline: float


@dataclass
class Room:
    room_id: str
    host: Account
    locked: bool = False
    grace: HostGrace | None = None
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
        return self.host_present_without(None)

    def host_present_without(self, participant_id: str | None) -> bool:
        """Whether a seat of the host's account other than the named one is in the room."""
        return any(
            self.is_hosted_by(participant.account)
            for participant in self.participants.values()
            if participant.participant_id != participant_id
        )

    @property
    def has_guests(self) -> bool:
        """Whether the call holds a seat other than the host's or a joined agent."""
        return bool(self.agent_ids) or any(not self.is_hosted_by(p.account) for p in self.participants.values())

    def is_hosted_by(self, account: Account) -> bool:
        return self.is_hosted_by_account_id(account.id)

    def is_hosted_by_account_id(self, account_id: str) -> bool:
        return account_id == self.host.id

    def has_free_seat_for(self, account: Account, seat_limit: int) -> bool:
        return self._has_free_place(host_place_reserved=not self.is_hosted_by(account), place_limit=seat_limit)

    def has_free_place_for_agent(self, place_limit: int) -> bool:
        return self._has_free_place(host_place_reserved=True, place_limit=place_limit)

    def _has_free_place(self, *, host_place_reserved: bool, place_limit: int) -> bool:
        """People and joined agents take the same places; an away host's place stays free for the host."""
        reserved = host_place_reserved and not self.host_present
        return self.participant_count + len(self.agent_ids) + int(reserved) < place_limit
