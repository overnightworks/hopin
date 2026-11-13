from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection

    from hopin.participation.frames import Offer


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


def membership_in(groups: Collection[str] | None, group: str) -> Membership:
    """The membership a groups read shows: none when the read failed."""
    if groups is None:
        return Membership.FAILED
    return Membership.MEMBER if group in groups else Membership.NOT_MEMBER
