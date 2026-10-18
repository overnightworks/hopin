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
        host_seat_reserved = not self.host_present and not self.is_hosted_by(account)
        return self.participant_count + int(host_seat_reserved) < seat_limit
