"""Whom a joined agent may hear: HopIn's rule, and the record it is told to.

The rule is that only the sponsor of a joined agent has a switch for it, which starts on when the sponsor brought the
agent and off when someone else added it. The record behind the port is the one truth of who hears whom; this module
tells it every decision and every end, reaches it only on the event loop, and keeps the sound sessions (handles) that
end with it.
"""

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from hopin.participation.vocabulary import HearingRefusal

if TYPE_CHECKING:
    from collections.abc import Callable

    from hopin.participation.chat_window import LineKind
    from hopin.participation.participants import DialedAgent, Person
    from hopin.signaling.frames import PeerId


@dataclass(frozen=True)
class HeardLine:
    """A finished sentence and the agents that hear it, with the line each of their windows takes."""

    hearers: frozenset[Person]
    kind: LineKind
    name: str
    text: str


class HearingRecord(Protocol):
    """The record of who may hear whom, in participation's terms: a seat is the speaker and a joined agent's person
    the member, in the call of a room."""

    def allow(self, room_id: str, seat: PeerId, agent: Person, *, allowed: bool) -> None:
        """Record the decision that the agent hears the seat, or no longer does."""

    def sensing(self, room_id: str, seat: PeerId) -> frozenset[Person]:
        """The agents whose switch for the seat is on."""

    def heard(self, room_id: str, seat: PeerId, name: str, text: str, began_at: float) -> HeardLine | None:
        """The agents that hear a finished sentence the seat said under the name, which began at `began_at`, with its
        line; nothing when the record refuses the sentence as no text that may be heard."""

    def member_ended(self, room_id: str, agent: Person) -> None:
        """The agent left the call or was removed from it."""

    def speaker_ended(self, room_id: str, seat: PeerId) -> None:
        """The seat left the call for good."""

    def place_ended(self, room_id: str) -> None:
        """The call ended."""


@dataclass(frozen=True, eq=False)
class HearingHandle:
    """The sound session of one seat: the account that holds it, the joined agent that serves it, and an event set when
    the session ends. Of the agents that hear the seat when it opens, the one that serves it holds a token, which the
    speech service takes; the session ends with that agent's hearing."""

    account: Person
    agent: Person
    seat: PeerId
    room_id: str
    ended: asyncio.Event = field(default_factory=asyncio.Event)


class Hearing:
    """The rule over the record. With `enabled` false no switch is ever turned on, so nothing is heard."""

    def __init__(self, record: HearingRecord, *, enabled: bool) -> None:
        self._record = record
        self.enabled = enabled
        self._handles: dict[PeerId, HearingHandle] = {}

    def joined(self, agent: DialedAgent, *, brought_by_sponsor: bool) -> None:
        """Record the default of a switch: on when the sponsor brought the agent, else off."""
        if self.enabled:
            self._record.allow(agent.room_id, agent.sponsor, agent.person, allowed=brought_by_sponsor)

    def switch_of(self, agent: DialedAgent) -> bool | None:
        """The agent's switch as the record holds it; none while the setting is off, when no agent has one."""
        if not self.enabled:
            return None
        return agent.person in self._record.sensing(agent.room_id, agent.sponsor)

    def switch(self, agent: DialedAgent, *, on: bool) -> None:
        """Set the sponsor's switch for the agent, which must be joined; nothing while the setting is off."""
        if not self.enabled:
            return
        self._record.allow(agent.room_id, agent.sponsor, agent.person, allowed=on)
        self._end_sound_unless_served(agent.sponsor)

    def open(
        self, account: Person, room_id: str, seat: PeerId, holds_token: Callable[[Person], bool]
    ) -> HearingHandle | HearingRefusal:
        """The sound session of the seat, which ends an older one; refused when no joined agent hears the seat. It is
        served by an agent that holds a token, else by the first of the agents that hear."""
        hearers = self._record.sensing(room_id, seat)
        if not hearers:
            return HearingRefusal.NOBODY_HEARS
        self.end_sound(seat)
        agent = min(hearers, key=lambda hearer: (not holds_token(hearer), hearer.issuer, hearer.subject))
        handle = HearingHandle(account, agent, seat, room_id)
        self._handles[seat] = handle
        return handle

    def hearers(self, room_id: str, seat: PeerId) -> frozenset[Person]:
        """The agents that hear the seat now."""
        return self._record.sensing(room_id, seat)

    def heard(self, room_id: str, seat: PeerId, name: str, text: str, began_at: float) -> HeardLine | None:
        """The agents that hear the sentence with its line; nothing for a sentence the record refuses."""
        return self._record.heard(room_id, seat, name, text, began_at)

    def agent_ended(self, agent: DialedAgent) -> None:
        """The agent's removal, leave or lease end: it hears nothing more."""
        self._record.member_ended(agent.room_id, agent.person)
        self._end_sound_unless_served(agent.sponsor)

    def seat_ended(self, room_id: str, seat: PeerId) -> None:
        """The seat left the call for good."""
        self._record.speaker_ended(room_id, seat)
        self.end_sound(seat)

    def end_sound(self, seat: PeerId) -> None:
        """End the seat's sound session, which a parked seat does without ending the record."""
        handle = self._handles.pop(seat, None)
        if handle is not None:
            handle.ended.set()

    def room_ended(self, room_id: str) -> None:
        """The call ended or the process shuts down."""
        self._record.place_ended(room_id)
        for seat in [seat for seat, handle in self._handles.items() if handle.room_id == room_id]:
            self.end_sound(seat)

    def _end_sound_unless_served(self, seat: PeerId) -> None:
        """End the seat's sound session when the agent that serves it no longer hears the seat."""
        handle = self._handles.get(seat)
        if handle is not None and handle.agent not in self._record.sensing(handle.room_id, seat):
            self.end_sound(seat)
