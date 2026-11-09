import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hopin.signaling.frames import PeerId


@dataclass(frozen=True)
class Attachment:
    """The table a room shows: its number (the revision that attached it), the address its frame loads and the seat
    that brought it, or nothing once that seat has left for good."""

    number: int
    source: str
    opener: PeerId | None


@dataclass(frozen=True)
class RoomState:
    """What a room shows now, with the revision it has had since its last change."""

    revision: int
    attachment: Attachment | None


@dataclass
class Stages:
    """The one table each room shows, and the revision every change of it is given; revisions rise for the whole
    process, so a later state always carries a higher one."""

    _attachments: dict[str, Attachment] = field(default_factory=dict)
    _revisions: dict[str, int] = field(default_factory=dict)
    _counter: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    def state_of(self, room_id: str) -> RoomState:
        return RoomState(self._revisions.get(room_id, 0), self._attachments.get(room_id))

    def attach(self, room_id: str, source: str, opener: PeerId) -> bool:
        """Show the table unless the room shows one; of two attaches, the first call wins."""
        if room_id in self._attachments:
            return False
        revision = next(self._counter)
        self._revisions[room_id] = revision
        self._attachments[room_id] = Attachment(revision, source, opener)
        return True

    def detach(self, room_id: str) -> None:
        self._attachments.pop(room_id)
        self._revisions[room_id] = next(self._counter)

    def forget_opener(self, peer_id: PeerId) -> None:
        """Clear the seat as the opener of every table it opened."""
        for room_id, held in [*self._attachments.items()]:
            if held.opener == peer_id:
                self._attachments[room_id] = Attachment(held.number, held.source, None)
                self._revisions[room_id] = next(self._counter)

    def end(self, room_id: str) -> None:
        self._attachments.pop(room_id, None)
        self._revisions.pop(room_id, None)
