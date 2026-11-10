"""The places agents hold in calls: an open invitation or a joined tile, one per agent and room.

A joined agent's place holds its chat window, which goes with the place.

The table is pure state with no clock and no I/O; its owner decides when an invitation ends. A removal, a
withdrawal or an expiry raises the epoch of its (agent, room), so an invitation made before it is stale.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from hopin.participation.chat_window import ChatWindow

if TYPE_CHECKING:
    from hopin.participation.feature import DialedAgent, Person
    from hopin.signaling.frames import PeerId

type PlaceKey = tuple[Person, str]


@dataclass(frozen=True)
class Invited:
    """An open invitation: bound to the owner's exact seat, opened by the adder's seat. `expires_at` is the wall-clock
    time the agent reads; `deadline` is the scheduler's time that enforces it."""

    invitation_id: str
    epoch: int
    expires_at: float
    deadline: float
    sponsor: PeerId
    adder: PeerId


type Place = Invited | DialedAgent


class PlaceState(StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    PAUSED = "paused"


@dataclass(frozen=True)
class PlaceStanding:
    """An agent's standing in one room as participation reads it: its state and, exactly when invited, the open
    invitation."""

    room_id: str
    state: PlaceState
    invitation: Invited | None = None


class Places:
    """One place per (agent, room), and an epoch per (agent, room) that only rises while the room lives."""

    def __init__(self) -> None:
        self._places: dict[PlaceKey, Place] = {}
        self._epochs: dict[PlaceKey, int] = {}
        self._windows: dict[PlaceKey, ChatWindow] = {}

    def get(self, person: Person, room_id: str) -> Place | None:
        return self._places.get((person, room_id))

    def epoch_of(self, person: Person, room_id: str) -> int:
        return self._epochs.get((person, room_id), 0)

    def put(self, person: Person, room_id: str, place: Place) -> None:
        """Hold the place, after every place held before it; a joined agent's place starts an empty window named by
        the agent's id."""
        self._places.pop((person, room_id), None)
        self._places[(person, room_id)] = place
        if not isinstance(place, Invited):
            self._windows[(person, room_id)] = ChatWindow(place.agent_id)

    def remove(self, person: Person, room_id: str) -> None:
        """Take the place, which must exist, away and raise its epoch."""
        del self._places[(person, room_id)]
        self._windows.pop((person, room_id), None)
        self._epochs[(person, room_id)] = self.epoch_of(person, room_id) + 1

    def drop_room(self, room_id: str) -> None:
        for key in [key for key in {*self._places, *self._epochs} if key[1] == room_id]:
            self._places.pop(key, None)
            self._epochs.pop(key, None)
            self._windows.pop(key, None)

    def clear(self) -> None:
        self._places.clear()
        self._epochs.clear()
        self._windows.clear()

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
        return [place for place in self._places.values() if not isinstance(place, Invited)]
