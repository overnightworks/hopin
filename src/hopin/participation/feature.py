import secrets
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import structlog

from hopin.errors import InvalidMessageError
from hopin.participation.vocabulary import (
    AGENT_ID_BYTES,
    AGENT_NAME_MAX_CHARACTERS,
    AGENTS_PER_PROCESS,
    AGENTS_PER_ROOM,
    FEATURE_NAME,
    SIGNAL_TYPES,
    AgentRefusal,
    ParticipationSignal,
)
from hopin.signaling.frames import TYPE_KEY, JsonObject, PeerId

if TYPE_CHECKING:
    from collections.abc import Mapping

    from hopin.signaling.features import CallRooms
    from hopin.signaling.frames import ForeignFrame

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

CONTROL_CHARACTER: Final = "Cc"


@dataclass(frozen=True)
class Person:
    """Who a token speaks for, as the issuer names them: the issuer and the subject, which is the account's id."""

    issuer: str
    subject: str


@dataclass(frozen=True)
class DialedAgent:
    """An agent in a call. Its person is whose token it uses; its sponsor is the seat that decides where it may be and
    when it goes. Today both are the one person who dialed it in."""

    agent_id: str
    name: str
    person: Person
    sponsor: PeerId
    room_id: str


type DialOutcome = DialedAgent | AgentRefusal


def _clean_name(name: str) -> str | None:
    """The name an agent shows under, or nothing when it is no name: 1 to 32 characters after trimming, none a
    control character."""
    trimmed = name.strip()
    if not 0 < len(trimmed) <= AGENT_NAME_MAX_CHARACTERS:
        return None
    if any(unicodedata.category(character) == CONTROL_CHARACTER for character in trimmed):
        return None
    return trimmed


def _joined_frame(agent: DialedAgent, sponsor_name: str) -> JsonObject:
    return {
        TYPE_KEY: ParticipationSignal.AGENT_JOINED.value,
        "agent_id": agent.agent_id,
        "name": agent.name,
        "sponsor": sponsor_name,
        "sponsor_id": agent.sponsor,
    }


def _left_frame(agent: DialedAgent) -> JsonObject:
    return {TYPE_KEY: ParticipationSignal.AGENT_LEFT.value, "agent_id": agent.agent_id}


def _refusal_frame(refusal: AgentRefusal) -> JsonObject:
    return {TYPE_KEY: ParticipationSignal.AGENT_REFUSED.value, "reason": refusal.value}


class Participation:
    """The agents dialed into calls: one per sponsoring seat, up to two per call and 32 in the process."""

    def __init__(self, rooms: CallRooms) -> None:
        self._rooms = rooms
        self._agents: dict[str, DialedAgent] = {}

    @property
    def name(self) -> str:
        return FEATURE_NAME

    @property
    def signal_types(self) -> Mapping[str, str]:
        return SIGNAL_TYPES

    async def dial_in(self, person: Person, room_id: str, name: str) -> DialOutcome:
        """Bind the person's agent to the person's one seat in the room; a repeat dial-in returns the same agent."""
        agent_name = _clean_name(name)
        if agent_name is None:
            return AgentRefusal.BAD_NAME
        seats = self._rooms.seats_in(room_id, person.subject)
        if not seats:
            return AgentRefusal.NO_SEAT
        if len(seats) > 1:
            return AgentRefusal.TWO_SEATS
        [sponsor] = seats
        if self._rooms.is_parked(sponsor):
            return AgentRefusal.SPONSOR_AWAY
        existing = self._agent_of(person, sponsor)
        if existing is not None:
            return existing
        if self._is_full(room_id):
            return AgentRefusal.BUSY
        agent = DialedAgent(secrets.token_hex(AGENT_ID_BYTES), agent_name, person, sponsor, room_id)
        self._agents[agent.agent_id] = agent
        await self._send_to_room(room_id, _joined_frame(agent, self._rooms.name_of(sponsor)))
        await logger.ainfo("agent_dialed_in", room_id=room_id, agent_id=agent.agent_id)
        return agent

    async def leave(self, person: Person, agent_id: str) -> AgentRefusal | None:
        """Take the person's agent out of its call; the refusal, or nothing when it is out."""
        agent = self._agents.get(agent_id)
        if agent is None:
            return AgentRefusal.GONE
        if agent.person != person:
            return AgentRefusal.NOT_YOURS
        if self._rooms.room_of(agent.sponsor) != agent.room_id:
            return AgentRefusal.GONE
        if self._rooms.is_parked(agent.sponsor):
            return AgentRefusal.SPONSOR_AWAY
        await self._remove(agent)
        return None

    async def on_frame(self, peer_id: PeerId, frame: ForeignFrame) -> bool:
        if frame.signal_type != ParticipationSignal.AGENT_REMOVE:
            return False
        agent_id = frame.data.get("agent_id")
        if not isinstance(agent_id, str):
            raise InvalidMessageError("A removal needs the agent's id")
        agent = self._agents.get(agent_id)
        if agent is None or self._rooms.room_of(peer_id) != agent.room_id:
            return True
        if peer_id != agent.sponsor and not self._rooms.is_host(peer_id):
            await self._rooms.send(peer_id, _refusal_frame(AgentRefusal.NOT_SPONSOR_OR_HOST))
            return True
        await self._remove(agent)
        return True

    async def seat_joined(self, peer_id: PeerId) -> None:
        """Show a seat that just joined the agents already in its room."""
        room_id = self._rooms.room_of(peer_id)
        for agent in [agent for agent in self._agents.values() if agent.room_id == room_id]:
            await self._rooms.send(peer_id, _joined_frame(agent, self._rooms.name_of(agent.sponsor)))

    async def seat_left(self, peer_id: PeerId, *, room_id: str, name: str) -> None:
        """A seat that left for good takes its agents with it."""
        del room_id, name
        for agent in [agent for agent in self._agents.values() if agent.sponsor == peer_id]:
            await self._remove(agent)

    def room_ended(self, room_id: str) -> None:
        """Drop the room's agents; the room's seats hear the end itself."""
        for agent_id in [agent.agent_id for agent in self._agents.values() if agent.room_id == room_id]:
            del self._agents[agent_id]

    async def shut_down(self) -> None:
        self._agents.clear()

    def _agent_of(self, person: Person, sponsor: PeerId) -> DialedAgent | None:
        return next(
            (agent for agent in self._agents.values() if agent.person == person and agent.sponsor == sponsor), None
        )

    def _is_full(self, room_id: str) -> bool:
        in_room = sum(1 for agent in self._agents.values() if agent.room_id == room_id)
        return in_room >= AGENTS_PER_ROOM or len(self._agents) >= AGENTS_PER_PROCESS

    async def _remove(self, agent: DialedAgent) -> None:
        del self._agents[agent.agent_id]
        await self._send_to_room(agent.room_id, _left_frame(agent))
        await logger.ainfo("agent_left", room_id=agent.room_id, agent_id=agent.agent_id)

    async def _send_to_room(self, room_id: str, frame: JsonObject) -> None:
        for peer_id in self._rooms.members(room_id):
            await self._rooms.send(peer_id, frame)
