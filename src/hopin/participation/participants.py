"""Who takes part in a call beside its people: the person a token speaks for, a tool's caller and an agent's place."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hopin.participation.vocabulary import AgentRefusal

if TYPE_CHECKING:
    from hopin.participation.figures import Figure
    from hopin.signaling.frames import PeerId


@dataclass(frozen=True)
class Person:
    """Who a token speaks for, as the issuer names them: the issuer and the subject, which is the account's id."""

    issuer: str
    subject: str


@dataclass(frozen=True)
class OwnAccount:
    """What an agent with an account of its own says about itself: its owner's subject and its name."""

    owner_subject: str
    name: str


@dataclass(frozen=True)
class Caller:
    """Who calls a tool: a person, and the account of its own when the caller is an agent that has one."""

    person: Person
    own_account: OwnAccount | None = None


@dataclass(frozen=True)
class DialedAgent:
    """An agent in a call. Its person is whose token it uses; its sponsor is the seat that decides where it may be and
    when it goes. Today both are the one person who dialed it in. Its voice is the speech voice its accept named and
    the connector knows, or none, which keeps the configured one."""

    agent_id: str
    name: str
    person: Person
    sponsor: PeerId
    room_id: str
    figure: Figure | None = None
    voice: str | None = None


type DialOutcome = DialedAgent | AgentRefusal
