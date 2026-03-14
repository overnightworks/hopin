from dataclasses import dataclass, field


@dataclass
class Participant:
    participant_id: str


@dataclass
class Room:
    room_id: str
    host_id: str
    password: str | None = None
    locked: bool = False
    participants: dict[str, Participant] = field(default_factory=dict)

    @property
    def participant_count(self) -> int:
        return len(self.participants)

    @property
    def participant_ids(self) -> list[str]:
        return list(self.participants.keys())

    @property
    def is_empty(self) -> bool:
        return self.participant_count == 0

    @property
    def has_password(self) -> bool:
        return self.password is not None

    def verify_password(self, password: str | None) -> bool:
        if self.password is None:
            return True
        return password == self.password
