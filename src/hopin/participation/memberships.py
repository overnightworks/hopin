from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection

    from hopin.participation.frames import Offer
    from hopin.signaling.frames import PeerId


class Membership(StrEnum):
    MEMBER = "member"
    NOT_MEMBER = "not_member"
    FAILED = "failed"


@dataclass
class SeatView:
    """What participation keeps of a seat in a room: its group memberships as last read and the offers last sent."""

    room_id: str
    memberships: dict[str, Membership] = field(default_factory=dict)
    offered: tuple[Offer, ...] = ()
    shown: bool = False
    full: bool = False


class SeatViews:
    """Participation's view of each seat it has seen join a room, kept until the seat leaves or its room ends; so it
    names the room a departing seat was last seen in."""

    def __init__(self) -> None:
        self._seats: dict[PeerId, SeatView] = {}

    def joined(self, peer_id: PeerId, room_id: str) -> None:
        self._seats[peer_id] = SeatView(room_id)

    def room_of(self, peer_id: PeerId) -> str | None:
        """The room the seat was last seen in, until its leave."""
        seat = self._seats.get(peer_id)
        return None if seat is None else seat.room_id

    def left(self, peer_id: PeerId) -> str:
        """Forget the seat; the room it sat in."""
        return self._seats.pop(peer_id).room_id

    def of(self, peer_id: PeerId, room_id: str) -> SeatView:
        """The seat's view in the room, kept from now on when it had none."""
        return self._seats.setdefault(peer_id, SeatView(room_id))

    def room_ended(self, room_id: str) -> None:
        for peer_id in [peer_id for peer_id, seat in self._seats.items() if seat.room_id == room_id]:
            del self._seats[peer_id]

    def clear(self) -> None:
        self._seats.clear()

    def rooms(self) -> list[str]:
        """The rooms with a seat, in a stable order."""
        return sorted({seat.room_id for seat in self._seats.values()})


def membership_in(groups: Collection[str] | None, group: str) -> Membership:
    """The membership a groups read shows: none when the read failed."""
    if groups is None:
        return Membership.FAILED
    return Membership.MEMBER if group in groups else Membership.NOT_MEMBER
