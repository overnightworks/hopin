"""Which agents with an account of their own are live: one lease per person, renewed by every call it makes.

The registry is immutable and takes the time as an argument; its owner arms the timers and acts on what it answers.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from hopin.participation.participants import Person


@dataclass(frozen=True)
class LiveAgent:
    """An agent's registration: whose it is, how it shows, the key its places hang on, and when its lease runs out.
    The generation tells a timer which registration it was armed for."""

    person: Person
    owner_subject: str
    name: str
    key: str
    deadline: float
    generation: int


@dataclass(frozen=True)
class Admission:
    """What one admitted call did: the registry after it, the live agent, the registration it ended (an expired lease
    or another owner's) and whether it started a registration that needs a timer."""

    agents: LiveAgents
    agent: LiveAgent
    ended: LiveAgent | None
    registered: bool


class TimerAction(StrEnum):
    NOTHING = "nothing"
    REARM = "rearm"
    EXPIRE = "expire"


@dataclass(frozen=True)
class TimerCheck:
    """What a timer found: the registry after it, the action, the registration it ended and the seconds to wait when
    it re-arms."""

    agents: LiveAgents
    action: TimerAction
    ended: LiveAgent | None = None
    rearm_after: float = 0.0


@dataclass(frozen=True)
class LiveAgents:
    """The live agents by person, and how many registrations were made."""

    by_person: Mapping[Person, LiveAgent] = field(default_factory=dict)
    registrations: int = 0

    def admit(
        self,
        person: Person,
        owner_subject: str,
        name: str,
        *,
        now: float,
        lease_seconds: float,
        new_key: Callable[[], str],
    ) -> Admission:
        """Admit a call: register the agent, or renew its lease. A lease that ran out before its timer did, or another
        owner, ends the old registration and starts a new one under a key from `new_key`."""
        current = self.by_person.get(person)
        deadline = now + lease_seconds
        if current is not None and current.owner_subject == owner_subject and now < current.deadline:
            renewed = LiveAgent(person, owner_subject, name, current.key, deadline, current.generation)
            return Admission(self._with(renewed), renewed, ended=None, registered=False)
        registered = LiveAgent(person, owner_subject, name, new_key(), deadline, self.registrations + 1)
        agents = LiveAgents({**self.by_person, person: registered}, self.registrations + 1)
        return Admission(agents, registered, ended=current, registered=True)

    def by_key(self, key: str) -> LiveAgent | None:
        return next((agent for agent in self.by_person.values() if agent.key == key), None)

    def checked(self, person: Person, generation: int, *, now: float) -> TimerCheck:
        """A timer's look: nothing for a registration that is gone or replaced, a re-arm before the deadline, else the
        end of the lease."""
        current = self.by_person.get(person)
        if current is None or current.generation != generation:
            return TimerCheck(self, TimerAction.NOTHING)
        if now < current.deadline:
            return TimerCheck(self, TimerAction.REARM, rearm_after=current.deadline - now)
        remaining = {gone: agent for gone, agent in self.by_person.items() if gone != person}
        return TimerCheck(LiveAgents(remaining, self.registrations), TimerAction.EXPIRE, ended=current)

    def _with(self, agent: LiveAgent) -> LiveAgents:
        return LiveAgents({**self.by_person, agent.person: agent}, self.registrations)
