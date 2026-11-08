import secrets
import unicodedata
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Final

import structlog

from hopin.errors import InvalidMessageError
from hopin.participation.live_agents import LiveAgents, TimerAction
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
from hopin.signaling.frames import TYPE_KEY, JsonObject, JsonValue, PeerId

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from hopin.participation.live_agents import LiveAgent
    from hopin.signaling.features import CallRooms
    from hopin.signaling.frames import ForeignFrame
    from hopin.signaling.scheduler import Scheduler

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

CONTROL_CHARACTER: Final = "Cc"


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


def _frame(signal: ParticipationSignal, **fields: JsonValue) -> JsonObject:
    return {TYPE_KEY: signal.value, **fields}


def _joined_frame(agent: DialedAgent, sponsor_name: str) -> JsonObject:
    return _frame(
        ParticipationSignal.AGENT_JOINED,
        agent_id=agent.agent_id,
        name=agent.name,
        sponsor=sponsor_name,
        sponsor_id=agent.sponsor,
    )


def _left_frame(agent: DialedAgent) -> JsonObject:
    return _frame(ParticipationSignal.AGENT_LEFT, agent_id=agent.agent_id)


def _refusal_frame(refusal: AgentRefusal) -> JsonObject:
    return _frame(ParticipationSignal.AGENT_REFUSED, reason=refusal.value)


class Participation:
    """The agents dialed into calls: one per sponsoring seat, up to two per call and 32 in the process."""

    def __init__(
        self,
        rooms: CallRooms,
        scheduler: Scheduler,
        lease_seconds: float,
        on_changed: Callable[[Person], None],
    ) -> None:
        self._rooms = rooms
        self._scheduler = scheduler
        self._lease_seconds = lease_seconds
        self._on_changed = on_changed
        self._live = LiveAgents()
        self._agents: dict[str, DialedAgent] = {}
        self._departed: dict[PeerId, list[DialedAgent]] = {}

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
        await self._announce_joined(agent, self._rooms.members(room_id), self._rooms.name_of(sponsor))
        await logger.ainfo("agent_dialed_in", room_id=room_id, agent_id=agent.agent_id)
        return agent

    async def leave(self, person: Person, room_id: str) -> AgentRefusal | None:
        """Take the person's live agent out of the room's call; the refusal, or nothing when it is out."""
        agent = next(
            (agent for agent in self._agents.values() if agent.person == person and agent.room_id == room_id),
            None,
        )
        if agent is None:
            return AgentRefusal.GONE
        if self._rooms.is_parked(agent.sponsor):
            return AgentRefusal.SPONSOR_AWAY
        await self._remove(agent)
        return None

    def standings_of(self, person: Person) -> tuple[str, ...]:
        """The rooms where the person holds a place of its own. An agent with an account of its own holds none yet."""
        del person
        return ()

    def agent_called(self, person: Person, own_account: OwnAccount) -> None:
        """An admitted call of an agent with an account of its own: renew its lease, or register it. A lease that ran
        out or another owner ends the old registration first."""
        admission = self._live.admit(
            person,
            own_account.owner_subject,
            own_account.name,
            now=self._scheduler.now(),
            lease_seconds=self._lease_seconds,
        )
        self._live = admission.agents
        if admission.ended is not None:
            self._end_lease(admission.ended)
        if admission.registered:
            self._arm(person, admission.agent.generation, self._lease_seconds)

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
            if self._in_call(agent):
                await self._announce_joined(agent, [peer_id], self._rooms.name_of(agent.sponsor))

    def seat_changed(self, peer_id: PeerId) -> None:
        """A seat that left its room takes its agents out of the state at once and holds them for `seat_left`."""
        if self._rooms.room_of(peer_id) is not None:
            return
        agents = [agent for agent in self._agents.values() if agent.sponsor == peer_id]
        for agent in agents:
            del self._agents[agent.agent_id]
        if agents:
            self._departed[peer_id] = agents

    async def seat_left(self, peer_id: PeerId, *, room_id: str, name: str) -> None:
        """Announce the tiles of the agents the seat took with it."""
        del room_id, name
        for agent in self._departed.pop(peer_id, []):
            await self._announce_removed(agent)

    def room_ended(self, room_id: str) -> None:
        """Drop the room's agents; the room's seats hear the end itself."""
        for sponsor in [sponsor for sponsor, gone in self._departed.items() if gone[0].room_id == room_id]:
            del self._departed[sponsor]
        for agent_id in [agent.agent_id for agent in self._agents.values() if agent.room_id == room_id]:
            del self._agents[agent_id]

    async def shut_down(self) -> None:  # NOSONAR(S7503) the call feature contract awaits it
        self._agents.clear()
        self._departed.clear()
        self._live = LiveAgents()

    def _arm(self, person: Person, generation: int, seconds: float) -> None:
        self._scheduler.call_later(seconds, partial(self._lease_timer, person, generation))

    async def _lease_timer(self, person: Person, generation: int) -> None:  # NOSONAR(S7503) the scheduler awaits it
        check = self._live.checked(person, generation, now=self._scheduler.now())
        self._live = check.agents
        if check.action == TimerAction.REARM:
            self._arm(person, generation, check.rearm_after)
        elif check.ended is not None:
            self._end_lease(check.ended)

    def _end_lease(self, ended: LiveAgent) -> None:
        """End every place of the agent in one synchronous step, then tell its waiters. It holds no place yet."""
        self._on_changed(ended.person)

    def _agent_of(self, person: Person, sponsor: PeerId) -> DialedAgent | None:
        return next(
            (agent for agent in self._agents.values() if agent.person == person and agent.sponsor == sponsor), None
        )

    def _is_full(self, room_id: str) -> bool:
        in_room = sum(1 for agent in self._agents.values() if agent.room_id == room_id)
        return in_room >= AGENTS_PER_ROOM or len(self._agents) >= AGENTS_PER_PROCESS

    def _in_call(self, agent: DialedAgent) -> bool:
        """The agent is registered, so its seat has not taken it away."""
        return agent.agent_id in self._agents

    async def _remove(self, agent: DialedAgent) -> None:
        del self._agents[agent.agent_id]
        await self._announce_removed(agent)

    async def _announce_removed(self, agent: DialedAgent) -> None:
        await self._send_to_room(agent.room_id, _left_frame(agent))
        await logger.ainfo("agent_left", room_id=agent.room_id, agent_id=agent.agent_id)

    async def _announce_joined(self, agent: DialedAgent, peer_ids: Sequence[str], sponsor_name: str) -> None:
        """Tell the seats of the agent's tile, each only while the agent is still in the call."""
        frame = _joined_frame(agent, sponsor_name)
        for peer_id in peer_ids:
            if not self._in_call(agent):
                return
            await self._rooms.send(peer_id, frame)

    async def _send_to_room(self, room_id: str, frame: JsonObject) -> None:
        for peer_id in self._rooms.members(room_id):
            await self._rooms.send(peer_id, frame)
