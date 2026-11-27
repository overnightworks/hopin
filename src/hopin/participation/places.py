"""The places agents hold in calls: an open invitation or a joined tile, one per agent and room.

A joined agent's place holds its chat window and the times of its recent lines and looks, which go with the place.

The table is pure state with no clock and no I/O; the calling standard (`agents.calling`) decides when an invitation
ends and the table writes the row it returns. A removal raises the epoch of its (agent, room), so an invitation made
before it is stale. An invitation's expiry, decline or an owner's leaving leaves the epoch as it is: a new invitation
is a new id.
"""

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, cast

from agents.calling import BookEntry, CallOutcome, Ended, OpenInvitation, expired, owner_left

from hopin.config.rate import Rate, admitted
from hopin.participation.chat_window import ChatWindow
from hopin.participation.look_rate import LOOK_RATE
from hopin.participation.participants import Person

if TYPE_CHECKING:
    from datetime import datetime

    from hopin.participation.participants import DialedAgent
    from hopin.signaling.frames import PeerId

type PlaceKey = tuple[Person, str]


def invitation_key(person: Person, room_id: str) -> str:
    """The (agent, room) of an invitation as its deadline's key."""
    return json.dumps((person.key, room_id))


def invitation_of(key: str) -> PlaceKey:
    person_key, room_id = json.loads(key)
    return Person.from_key(person_key), room_id


@dataclass(frozen=True)
class AbandonedInvitation:
    """An invitation that ended because the owner's seat left: the room, the agent's key and name, the seat that made
    it and the outcome it is told."""

    room_id: str
    agent_key: str
    name: str
    inviter: PeerId
    outcome: CallOutcome


type Place = OpenInvitation | DialedAgent


class PlaceState(StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    PAUSED = "paused"


@dataclass(frozen=True)
class InvitationStanding:
    """What the agent reads of its open invitation: `expires_at` is the wall-clock time the invitation ends."""

    invitation_id: str
    epoch: int
    expires_at: datetime


@dataclass(frozen=True)
class PlaceStanding:
    """An agent's standing in one room as participation reads it: its state and, exactly when invited, the open
    invitation."""

    room_id: str
    state: PlaceState
    invitation: InvitationStanding | None = None


class Places:
    """One place per (agent, room), and an epoch per (agent, room) that only rises while the room lives."""

    def __init__(self) -> None:
        self._places: dict[PlaceKey, Place] = {}
        self._epochs: dict[PlaceKey, int] = {}
        self._windows: dict[PlaceKey, ChatWindow] = {}
        self._look_times: dict[PlaceKey, tuple[float, ...]] = {}

    def get(self, person: Person, room_id: str) -> Place | None:
        return self._places.get((person, room_id))

    def epoch_of(self, person: Person, room_id: str) -> int:
        return self._epochs.get((person, room_id), 0)

    def entry(self, person: Person, room_id: str) -> BookEntry:
        """The agent's row at the room as the calling standard reads it: the epoch and the open invitation, if any."""
        place = self.get(person, room_id)
        return BookEntry(
            epoch=self.epoch_of(person, room_id), invitation=place if isinstance(place, OpenInvitation) else None
        )

    def write(self, person: Person, room_id: str, entry: BookEntry) -> None:
        """Hold the row the calling standard returned: its epoch, and its invitation as the place, or no place when
        the invitation ended."""
        self._epochs[(person, room_id)] = entry.epoch
        if entry.invitation is not None:
            self._places[(person, room_id)] = entry.invitation
        else:
            del self._places[(person, room_id)]

    def end_for_departed_owner(self, person: Person, room_id: str, now: datetime) -> Ended:
        """End the agent's invitation at the room, which is held, because its owner's seat left: owner away when it is
        open, no answer when the wall clock lapsed it before its timer ran. The epoch stays."""
        entry = self.entry(person, room_id)
        ended = cast("Ended", owner_left(entry, now=now) or expired(entry, now=now))
        self.write(person, room_id, ended.entry)
        return ended

    def bound_to(self, seat: PeerId) -> list[tuple[Person, str, Place]]:
        """The places bound to the seat: an invitation to the owner's seat it names, an agent to its sponsor."""
        return [
            (person, room_id, place)
            for (person, room_id), place in self._places.items()
            if (place.bound_to if isinstance(place, OpenInvitation) else place.sponsor) == seat
        ]

    def put(self, person: Person, room_id: str, agent: DialedAgent) -> None:
        """Hold the joined agent's place, which starts an empty window named by the agent's id."""
        self._places[(person, room_id)] = agent
        self._windows[(person, room_id)] = ChatWindow(agent.agent_id)
        self._look_times[(person, room_id)] = ()

    def remove(self, person: Person, room_id: str) -> None:
        """Take the place, which must exist, away and raise its epoch."""
        del self._places[(person, room_id)]
        self._windows.pop((person, room_id), None)
        self._look_times.pop((person, room_id), None)
        self._epochs[(person, room_id)] = self.epoch_of(person, room_id) + 1

    def drop_room(self, room_id: str) -> None:
        for key in [key for key in {*self._places, *self._epochs} if key[1] == room_id]:
            self._places.pop(key, None)
            self._epochs.pop(key, None)
            self._windows.pop(key, None)
            self._look_times.pop(key, None)

    def clear(self) -> None:
        self._places.clear()
        self._epochs.clear()
        self._windows.clear()
        self._look_times.clear()

    def take_look(self, person: Person, room_id: str, now: float) -> bool:
        """Count a look of the person's joined agent at `now` among its recent ones; false, and none counted, when it
        has taken the most looks a window allows."""
        return self._take(self._look_times, (person, room_id), now, LOOK_RATE)

    @staticmethod
    def _take(times_of: dict[PlaceKey, tuple[float, ...]], key: PlaceKey, now: float, rate: Rate) -> bool:
        times = admitted(times_of[key], now, rate)
        if times is None:
            return False
        times_of[key] = times
        return True

    def window_of(self, person: Person, room_id: str) -> ChatWindow | None:
        return self._windows.get((person, room_id))

    def window_rooms_of(self, person: Person) -> tuple[str, ...]:
        """The rooms where the person holds a window."""
        return tuple(room_id for (holder, room_id) in self._windows if holder == person)

    def windows_in(self, room_id: str) -> dict[Person, ChatWindow]:
        """The window of every agent joined in the room, by the person whose agent it is."""
        return {person: window for (person, held_room), window in self._windows.items() if held_room == room_id}

    def entries(self) -> list[tuple[Person, str, Place]]:
        return [(person, room_id, place) for (person, room_id), place in self._places.items()]

    def of_person(self, person: Person) -> list[tuple[str, Place]]:
        return [(room_id, place) for (holder, room_id), place in self._places.items() if holder == person]

    def joined(self) -> list[DialedAgent]:
        return [place for place in self._places.values() if not isinstance(place, OpenInvitation)]
