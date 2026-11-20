from dataclasses import KW_ONLY, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hopin.signaling.frames import PeerId


@dataclass(frozen=True)
class Attachment:
    """The table a room shows: its number (the revision that attached it), its app's name, its id, the address its frame
    loads and the seat that brought it, or nothing once that seat has left for good."""

    number: int
    _: KW_ONLY
    app: str
    table: str
    source: str
    opener: PeerId | None


@dataclass(frozen=True)
class RoomState:
    """What a room shows now, with the revision it has had since its last change; every change gives the room a
    higher revision."""

    revision: int = 0
    attachment: Attachment | None = None

    def attached(self, *, app: str, table: str, source: str, opener: PeerId) -> RoomState | None:
        """The room showing the table, or nothing while it shows one already."""
        if self.attachment is not None:
            return None
        revision = self.revision + 1
        return RoomState(revision, Attachment(revision, app=app, table=table, source=source, opener=opener))

    def detached(self) -> RoomState:
        return RoomState(self.revision + 1, None)

    def without_opener(self, peer_id: PeerId) -> RoomState:
        """The room's table with the seat no longer its opener; unchanged when the seat opened none."""
        held = self.attachment
        if held is None or held.opener != peer_id:
            return self
        return RoomState(
            self.revision + 1, Attachment(held.number, app=held.app, table=held.table, source=held.source, opener=None)
        )
