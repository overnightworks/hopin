import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hopin.signaling.transition import Work

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import HTTPConnection

    from hopin.participation.live_agents import LiveAgent
    from hopin.signaling.frames import PeerId

type GroupsOf = Callable[[HTTPConnection], Awaitable[frozenset[str] | None]]
"""The groups the issuer holds for the session a connection carries now, or nothing when they cannot be read."""


GROUPS_READ_TASK_NAME = "participation-groups-read"
"""The start of every groups read's key, which names the read's task."""


@dataclass(frozen=True)
class GroupsRead(Work):
    """A read of the groups of the seat's session, as work on the process that holds the seat's socket: for the
    offers, or for the add of the agent registered as `adding`, whose owner sits in the room as `sponsor`."""

    adding: LiveAgent | None = None
    sponsor: PeerId | None = None


class GroupReads:
    """Names the reads of seats' groups: one read for the offers runs at a time per seat, and each add reads afresh."""

    def __init__(self) -> None:
        self._adds = itertools.count()

    @staticmethod
    def for_offers(peer_id: PeerId) -> GroupsRead:
        return GroupsRead(f"{GROUPS_READ_TASK_NAME}:{peer_id}", peer_id)

    def for_add(self, peer_id: PeerId, adding: LiveAgent, sponsor: PeerId) -> GroupsRead:
        return GroupsRead(f"{GROUPS_READ_TASK_NAME}:{peer_id}:add:{next(self._adds)}", peer_id, adding, sponsor)
