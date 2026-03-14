from dataclasses import dataclass, field


@dataclass
class Viewer:
    viewer_id: str


@dataclass
class Room:
    room_id: str
    streamer_id: str
    viewers: dict[str, Viewer] = field(default_factory=dict)

    @property
    def viewer_count(self) -> int:
        return len(self.viewers)
