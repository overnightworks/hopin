import asyncio
from typing import TYPE_CHECKING

from hopin.participation.memberships import Membership, membership_in

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from starlette.requests import HTTPConnection

    from hopin.participation.memberships import SeatView
    from hopin.signaling.features import CallRooms
    from hopin.signaling.frames import PeerId

type GroupsOf = Callable[[HTTPConnection], Awaitable[frozenset[str] | None]]
"""The groups the issuer holds for the session a connection carries now, or nothing when they cannot be read."""


GROUPS_READ_TASK_NAME = "participation-groups-read"


class GroupReads:
    """The reads of a seat's groups: one runs at a time per seat and room, and its result lands in the seat's view."""

    def __init__(
        self,
        rooms: CallRooms,
        adders: Mapping[str, str],
        groups_of: GroupsOf,
        view_of: Callable[[PeerId, str], SeatView],
        read_ended: Callable[[str], Awaitable[None]],
    ) -> None:
        self._rooms = rooms
        self._adders = adders
        self._groups_of = groups_of
        self._view_of = view_of
        self._read_ended = read_ended
        self._reads: dict[tuple[PeerId, str], asyncio.Task[None]] = {}

    def start(self, room_id: str, seats: Sequence[PeerId]) -> None:
        """Read the groups of each seat whose membership in an adders group is unknown or failed to read."""
        groups = set(self._adders.values())
        for peer_id in seats:
            memberships = self._view_of(peer_id, room_id).memberships
            if any(memberships.get(group, Membership.FAILED) is Membership.FAILED for group in groups):
                self._start_one(peer_id, room_id)

    async def cancel(self) -> None:
        reads = list(self._reads.values())
        for read in reads:
            read.cancel()
        await asyncio.gather(*reads, return_exceptions=True)

    def _start_one(self, peer_id: PeerId, room_id: str) -> None:
        key = (peer_id, room_id)
        if key in self._reads:
            return
        read = asyncio.create_task(
            self._read(peer_id, room_id, self._rooms.connection_of(peer_id)), name=GROUPS_READ_TASK_NAME
        )
        self._reads[key] = read
        read.add_done_callback(lambda _: self._reads.pop(key, None))

    async def _read(self, peer_id: PeerId, room_id: str, connection: HTTPConnection) -> None:
        groups = await self._groups_of(connection)
        if self._rooms.room_of(peer_id) != room_id:
            return
        memberships = self._view_of(peer_id, room_id).memberships
        for group in set(self._adders.values()):
            memberships[group] = membership_in(groups, group)
        await self._read_ended(room_id)
