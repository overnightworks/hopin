import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hopin.signaling.transition import Work

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import HTTPConnection

    from hopin.participation.live_agents import LiveAgent
    from hopin.participation.participants import DialedAgent
    from hopin.signaling.frames import PeerId

type GroupsOf = Callable[[HTTPConnection], Awaitable[frozenset[str] | None]]
"""The groups the issuer holds for the session a connection carries now, or nothing when they cannot be read."""


GROUPS_READ_TASK_NAME = "participation-groups-read"
"""The start of every groups read's key, which names the read's task."""


@dataclass(frozen=True)
class Adding:
    """An add a groups read decides: the registered agent to add."""

    agent: LiveAgent


@dataclass(frozen=True)
class SendingAway:
    """A send-away a groups read decides: the joined agent the press named."""

    agent: DialedAgent


@dataclass(frozen=True)
class GroupsRead(Work):
    """A read of the groups of the seat's session, as work on the process that holds the seat's socket: for the
    offers, or for the act it names, an add or a send-away."""

    act: Adding | SendingAway | None = None


class GroupReads:
    """Names the reads of seats' groups: one read for the offers runs at a time per seat, and each press reads
    afresh."""

    def __init__(self) -> None:
        self._presses = itertools.count()

    @staticmethod
    def for_offers(peer_id: PeerId) -> GroupsRead:
        return GroupsRead(f"{GROUPS_READ_TASK_NAME}:{peer_id}", peer_id)

    def for_add(self, peer_id: PeerId, adding: LiveAgent) -> GroupsRead:
        return self._for_press(peer_id, "add", Adding(adding))

    def for_send_away(self, peer_id: PeerId, agent: DialedAgent) -> GroupsRead:
        return self._for_press(peer_id, "away", SendingAway(agent))

    def _for_press(self, peer_id: PeerId, kind: str, act: Adding | SendingAway) -> GroupsRead:
        return GroupsRead(f"{GROUPS_READ_TASK_NAME}:{peer_id}:{kind}:{next(self._presses)}", peer_id, act)
