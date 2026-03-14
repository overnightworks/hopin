from dataclasses import dataclass, field


@dataclass
class Participant:
    participant_id: str


@dataclass
class Room:
    room_id: str
    host_id: str
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
