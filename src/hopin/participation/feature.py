import asyncio
import secrets
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from typing import TYPE_CHECKING, Final

import structlog

from hopin.errors import InvalidMessageError, RoomNotFoundError
from hopin.participation.live_agents import LiveAgents, TimerAction
from hopin.participation.places import Invited, Places, PlaceStanding, PlaceState
from hopin.participation.vocabulary import (
    AGENT_ID_BYTES,
    AGENT_NAME_MAX_CHARACTERS,
    AGENTS_PER_PROCESS,
    AGENTS_PER_ROOM,
    FEATURE_NAME,
    INVITATION_SECONDS,
    SIGNAL_TYPES,
    AgentRefusal,
    ParticipationSignal,
)
from hopin.signaling.frames import TYPE_KEY, JsonObject, JsonValue, PeerId

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from starlette.requests import HTTPConnection

    from hopin.participation.live_agents import LiveAgent
    from hopin.signaling.features import CallRooms
    from hopin.signaling.frames import ForeignFrame
    from hopin.signaling.scheduler import Scheduler

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

CONTROL_CHARACTER: Final = "Cc"
AGENT_ADDED_EVENT: Final = "agent_invited"

type GroupsOf = Callable[[HTTPConnection], Awaitable[frozenset[str] | None]]
"""The groups the issuer holds for the session a connection carries now, or nothing when they cannot be read."""


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


class Membership(StrEnum):
    MEMBER = "member"
    NOT_MEMBER = "not_member"
    FAILED = "failed"


@dataclass(frozen=True)
class Offer:
    """An agent a seat may add: the agent's key, its name, and whether this seat's invitation of it is open."""

    agent_key: str
    name: str
    pending: bool


@dataclass
class _SeatView:
    """What participation keeps of a seat in a room: its group memberships as last read and the offers last sent."""

    room_id: str
    memberships: dict[str, Membership] = field(default_factory=dict)
    offered: tuple[Offer, ...] = ()


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


def _offers_frame(offers: Sequence[Offer]) -> JsonObject:
    return _frame(
        ParticipationSignal.AGENT_OFFERS,
        offers=[{"agent_key": offer.agent_key, "name": offer.name, "pending": offer.pending} for offer in offers],
    )


def _refusal_frame(refusal: AgentRefusal) -> JsonObject:
    return _frame(ParticipationSignal.AGENT_REFUSED, reason=refusal.value)


class Participation:
    """The agents in calls: dialed in by a person's own agent or added by a member of the owner's adders group, one per
    sponsoring seat, up to two per call and 32 in the process."""

    def __init__(
        self,
        rooms: CallRooms,
        scheduler: Scheduler,
        lease_seconds: float,
        on_changed: Callable[[Person], None],
        wall_clock: Callable[[], float],
        adders: Mapping[str, str],
        groups_of: GroupsOf,
    ) -> None:
        self._rooms = rooms
        self._scheduler = scheduler
        self._lease_seconds = lease_seconds
        self._on_changed = on_changed
        self._wall_clock = wall_clock
        self._adders = adders
        self._groups_of = groups_of
        self._live = LiveAgents()
        self._places = Places()
        self._departed: dict[PeerId, list[DialedAgent]] = {}
        self._views: dict[PeerId, _SeatView] = {}
        self._reads: dict[tuple[PeerId, str], asyncio.Task[None]] = {}

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
        existing = self._places.get(person, room_id)
        if isinstance(existing, DialedAgent) and existing.sponsor == sponsor:
            return existing
        if self._is_busy(room_id, sponsor):
            return AgentRefusal.BUSY
        agent = DialedAgent(secrets.token_hex(AGENT_ID_BYTES), agent_name, person, sponsor, room_id)
        self._places.put(person, room_id, agent)
        self._on_changed(person)
        await self._announce_joined(agent, self._rooms.members(room_id), self._rooms.name_of(sponsor))
        await logger.ainfo("agent_dialed_in", room_id=room_id, agent_id=agent.agent_id)
        await self._update_offers(room_id)
        return agent

    async def leave(self, person: Person, room_id: str) -> AgentRefusal | None:
        """Take the person's agent out of the room's call; the refusal, or nothing when it is out. An open invitation
        is no place to leave."""
        agent = self._places.get(person, room_id)
        if not isinstance(agent, DialedAgent):
            return AgentRefusal.GONE
        if self._rooms.is_parked(agent.sponsor):
            return AgentRefusal.SPONSOR_AWAY
        await self._remove(agent)
        return None

    def standings_of(self, person: Person) -> tuple[PlaceStanding, ...]:
        """Every place the person holds as it reads now; a place it was removed from is absent."""
        return tuple(self._standing_in(room_id, place) for room_id, place in self._places.of_person(person))

    async def accept(self, person: Person, room_id: str, invitation_id: str, epoch: int) -> DialOutcome:
        """Turn the agent's open invitation into its tile, bound to the owner's seat the invitation names."""
        live = self._live.by_person.get(person)
        invitation = self._places.get(person, room_id)
        if (
            live is None
            or not isinstance(invitation, Invited)
            or invitation.invitation_id != invitation_id
            or epoch != self._places.epoch_of(person, room_id)
            or self._scheduler.now() >= invitation.deadline
        ):
            return AgentRefusal.STALE_INVITATION
        if self._rooms.seats_in(room_id, live.owner_subject) != [invitation.sponsor]:
            return AgentRefusal.TWO_SEATS
        if self._rooms.is_parked(invitation.sponsor):
            return AgentRefusal.SPONSOR_AWAY
        if self._is_busy(room_id, invitation.sponsor):
            return AgentRefusal.BUSY
        agent = DialedAgent(live.key, live.name, person, invitation.sponsor, room_id)
        self._places.put(person, room_id, agent)
        self._on_changed(person)
        await logger.ainfo("agent_invitation_ended", room_id=room_id, reason="accepted")
        await self._announce_joined(agent, self._rooms.members(room_id), self._rooms.name_of(invitation.sponsor))
        await self._update_offers(room_id)
        return agent

    async def agent_called(self, person: Person, own_account: OwnAccount) -> None:
        """An admitted call of an agent with an account of its own: renew its lease, or register it. A lease that ran
        out or another owner ends the old registration first."""
        admission = self._live.admit(
            person,
            own_account.owner_subject,
            own_account.name,
            now=self._scheduler.now(),
            lease_seconds=self._lease_seconds,
            new_key=partial(secrets.token_hex, AGENT_ID_BYTES),
        )
        self._live = admission.agents
        if admission.registered:
            self._arm(person, admission.agent.generation, self._lease_seconds)
        if admission.ended is not None:
            await self._end_lease(admission.ended)
        elif admission.registered:
            await self._update_every_room_offers()

    async def on_frame(self, peer_id: PeerId, frame: ForeignFrame) -> bool:
        match frame.signal_type:
            case ParticipationSignal.AGENT_REMOVE:
                await self._on_remove(peer_id, frame)
            case ParticipationSignal.AGENT_ADD:
                await self._on_add(peer_id, frame)
            case _:
                return False
        return True

    async def seat_joined(self, peer_id: PeerId) -> None:
        """Show a seat that just joined the agents already in its room, and the agents it may add."""
        room_id = self._rooms.room_of(peer_id)
        if room_id is None:  # pragma: no cover - a seat is in its room when it is announced
            return
        self._views[peer_id] = _SeatView(room_id)
        for agent in [agent for agent in self._places.joined() if agent.room_id == room_id]:
            if self._in_call(agent):
                await self._announce_joined(agent, [peer_id], self._rooms.name_of(agent.sponsor))
        await self._update_offers(room_id)

    def seat_changed(self, peer_id: PeerId) -> None:
        """A seat that left its room takes its places out of the state at once and holds its agents for `seat_left`;
        a seat that left, parked or resumed tells the agents it sponsors."""
        bound = [
            (person, room_id, place) for person, room_id, place in self._places.entries() if place.sponsor == peer_id
        ]
        if self._rooms.room_of(peer_id) is None:
            self._views.pop(peer_id, None)
            for person, room_id, _ in bound:
                self._places.remove(person, room_id)
            joined = [place for _, _, place in bound if isinstance(place, DialedAgent)]
            if joined:
                self._departed[peer_id] = joined
        for person in {person for person, _, _ in bound}:
            self._on_changed(person)

    async def seat_left(self, peer_id: PeerId, *, room_id: str, name: str) -> None:
        """Announce the tiles of the agents the seat took with it, and the agents the others may add now."""
        del name
        for agent in self._departed.pop(peer_id, []):
            await self._announce_removed(agent, self._recipients(agent.room_id))
        await self._update_offers(room_id)

    def room_ended(self, room_id: str) -> None:
        """Drop the room's places and seats; the room's seats hear the end itself."""
        for sponsor in [sponsor for sponsor, gone in self._departed.items() if gone[0].room_id == room_id]:
            del self._departed[sponsor]
        for peer_id in [peer_id for peer_id, view in self._views.items() if view.room_id == room_id]:
            del self._views[peer_id]
        holders = {person for person, held_room, _ in self._places.entries() if held_room == room_id}
        self._places.drop_room(room_id)
        for person in holders:
            self._on_changed(person)

    async def shut_down(self) -> None:
        reads = list(self._reads.values())
        for read in reads:
            read.cancel()
        await asyncio.gather(*reads, return_exceptions=True)
        self._places.clear()
        self._departed.clear()
        self._views.clear()
        self._live = LiveAgents()

    async def _on_remove(self, peer_id: PeerId, frame: ForeignFrame) -> None:
        agent_id = frame.data.get("agent_id")
        if not isinstance(agent_id, str):
            raise InvalidMessageError("A removal needs the agent's id")
        room_id = self._rooms.room_of(peer_id)
        agent = next(
            (agent for agent in self._places.joined() if agent.agent_id == agent_id and agent.room_id == room_id), None
        )
        if agent is None:
            return
        if peer_id != agent.sponsor and not self._rooms.is_host(peer_id):
            await self._rooms.send(peer_id, _refusal_frame(AgentRefusal.NOT_SPONSOR_OR_HOST))
            return
        await self._remove(agent)

    async def _on_add(self, peer_id: PeerId, frame: ForeignFrame) -> None:
        agent_key = frame.data.get("agent_key")
        if not isinstance(agent_key, str):
            raise InvalidMessageError("An add needs the agent's key")
        room_id = self._rooms.room_of(peer_id)
        live = None if room_id is None else self._live.by_key(agent_key)
        if room_id is None or live is None:
            await self._rooms.send(peer_id, _refusal_frame(AgentRefusal.GONE))
            return
        refusal = self._refusal_of_add(peer_id, room_id, live)
        if refusal is not None:
            await self._rooms.send(peer_id, _refusal_frame(refusal))
            return
        await self._add_after_a_fresh_read(peer_id, room_id, live)

    async def _add_after_a_fresh_read(self, peer_id: PeerId, room_id: str, live: LiveAgent) -> None:
        [sponsor] = self._rooms.seats_in(room_id, live.owner_subject)  # the checks before it found exactly one
        groups = await self._groups_of(self._rooms.connection_of(peer_id))
        if self._rooms.room_of(peer_id) != room_id:
            return
        group = self._adders[live.owner_subject]
        memberships = self._view_of(peer_id, room_id).memberships
        refusal: AgentRefusal | None
        if groups is None:
            memberships[group] = Membership.FAILED
            refusal = AgentRefusal.UNAVAILABLE
        else:
            memberships[group] = Membership.MEMBER if group in groups else Membership.NOT_MEMBER
            refusal = self._refusal_of_add_now(peer_id, room_id, live, sponsor)
        if refusal is not None:
            await self._rooms.send(peer_id, _refusal_frame(refusal))
        else:
            await self._invite(peer_id, room_id, live, sponsor)
        await self._update_offers(room_id)

    def _refusal_of_add_now(
        self, peer_id: PeerId, room_id: str, live: LiveAgent, sponsor: PeerId
    ) -> AgentRefusal | None:
        """The add as it stands after the fresh read: the same registration and the same single seat of the owner."""
        current = self._live.by_person.get(live.person)
        if current is None or current.generation != live.generation:
            return AgentRefusal.GONE
        refusal = self._refusal_of_add(peer_id, room_id, live)
        if refusal is None and self._rooms.seats_in(room_id, live.owner_subject) != [sponsor]:
            return AgentRefusal.NO_SEAT
        return refusal

    def _refusal_of_add(self, peer_id: PeerId, room_id: str, live: LiveAgent) -> AgentRefusal | None:
        sponsor = self._sole_seat(room_id, live.owner_subject)
        if isinstance(sponsor, AgentRefusal):
            return sponsor
        if self._rooms.is_parked(sponsor):
            return AgentRefusal.SPONSOR_AWAY
        group = self._adders.get(live.owner_subject)
        if group is None or self._view_of(peer_id, room_id).memberships.get(group) is Membership.NOT_MEMBER:
            return AgentRefusal.NOT_ADDER
        if self._places.get(live.person, room_id) is not None:
            return AgentRefusal.ALREADY_THERE
        if self._is_busy(room_id, sponsor):
            return AgentRefusal.BUSY
        return None

    async def _invite(self, adder: PeerId, room_id: str, live: LiveAgent, sponsor: PeerId) -> None:
        invitation = Invited(
            invitation_id=secrets.token_hex(AGENT_ID_BYTES),
            epoch=self._places.epoch_of(live.person, room_id),
            expires_at=self._wall_clock() + INVITATION_SECONDS,
            deadline=self._scheduler.now() + INVITATION_SECONDS,
            sponsor=sponsor,
            adder=adder,
        )
        self._places.put(live.person, room_id, invitation)
        self._scheduler.call_later(
            INVITATION_SECONDS, partial(self._invitation_timer, live.person, room_id, invitation.invitation_id)
        )
        self._on_changed(live.person)
        await logger.ainfo(AGENT_ADDED_EVENT, room_id=room_id)

    async def _invitation_timer(self, person: Person, room_id: str, invitation_id: str) -> None:
        place = self._places.get(person, room_id)
        if not isinstance(place, Invited) or place.invitation_id != invitation_id:
            return
        self._places.remove(person, room_id)
        self._on_changed(person)
        await logger.ainfo("agent_invitation_ended", room_id=room_id, reason="expired")
        await self._update_offers(room_id)

    def _arm(self, person: Person, generation: int, seconds: float) -> None:
        self._scheduler.call_later(seconds, partial(self._lease_timer, person, generation))

    async def _lease_timer(self, person: Person, generation: int) -> None:
        check = self._live.checked(person, generation, now=self._scheduler.now())
        self._live = check.agents
        if check.action == TimerAction.REARM:
            self._arm(person, generation, check.rearm_after)
        elif check.ended is not None:
            await self._end_lease(check.ended)

    async def _end_lease(self, ended: LiveAgent) -> None:
        """End every place of the agent in one synchronous step, then tell the rooms and its waiters."""
        departures: list[tuple[DialedAgent, list[PeerId]]] = []
        for room_id, place in self._places.of_person(ended.person):
            self._places.remove(ended.person, room_id)
            if isinstance(place, DialedAgent):
                departures.append((place, self._recipients(room_id)))
        self._on_changed(ended.person)
        for agent, recipients in departures:
            await self._announce_removed(agent, recipients)
        await self._update_every_room_offers()

    def _standing_in(self, room_id: str, place: Invited | DialedAgent) -> PlaceStanding:
        if isinstance(place, Invited):
            return PlaceStanding(room_id, PlaceState.INVITED, place)
        state = PlaceState.PAUSED if self._rooms.is_parked(place.sponsor) else PlaceState.ACTIVE
        return PlaceStanding(room_id, state)

    def _sole_seat(self, room_id: str, owner_subject: str) -> PeerId | AgentRefusal:
        seats = self._rooms.seats_in(room_id, owner_subject)
        if not seats:
            return AgentRefusal.NO_SEAT
        if len(seats) > 1:
            return AgentRefusal.TWO_SEATS
        return seats[0]

    def _is_busy(self, room_id: str, sponsor: PeerId) -> bool:
        """A seat sponsors one agent, a call holds two and the process 32."""
        joined = self._places.joined()
        in_room = sum(1 for agent in joined if agent.room_id == room_id)
        return (
            in_room >= AGENTS_PER_ROOM or len(joined) >= AGENTS_PER_PROCESS or any(a.sponsor == sponsor for a in joined)
        )

    def _in_call(self, agent: DialedAgent) -> bool:
        """The agent holds its place, so its seat has not taken it away."""
        return self._places.get(agent.person, agent.room_id) == agent

    def _view_of(self, peer_id: PeerId, room_id: str) -> _SeatView:
        return self._views.setdefault(peer_id, _SeatView(room_id))

    def _recipients(self, room_id: str) -> list[PeerId]:
        """The room's seats now, none once the room has ended."""
        try:
            return [PeerId(peer_id) for peer_id in self._rooms.members(room_id)]
        except RoomNotFoundError:
            return []

    async def _remove(self, agent: DialedAgent) -> None:
        recipients = self._recipients(agent.room_id)
        self._places.remove(agent.person, agent.room_id)
        self._on_changed(agent.person)
        await self._announce_removed(agent, recipients)
        await self._update_offers(agent.room_id)

    async def _announce_removed(self, agent: DialedAgent, recipients: Sequence[str]) -> None:
        await self._tell(agent.room_id, recipients, _left_frame(agent))
        await logger.ainfo("agent_left", room_id=agent.room_id, agent_id=agent.agent_id)

    async def _announce_joined(self, agent: DialedAgent, peer_ids: Sequence[str], sponsor_name: str) -> None:
        """Tell the seats of the agent's tile, each only while the agent is still in the call."""
        frame = _joined_frame(agent, sponsor_name)
        for peer_id in peer_ids:
            if not self._in_call(agent):
                return
            await self._tell(agent.room_id, [peer_id], frame)

    async def _tell(self, room_id: str, peer_ids: Sequence[str], frame: JsonObject) -> None:
        """Send to each seat that still sits in the room: a room that has ended gets nothing more."""
        for peer_id in peer_ids:
            if self._rooms.room_of(PeerId(peer_id)) == room_id:
                await self._rooms.send(peer_id, frame)

    async def _update_every_room_offers(self) -> None:
        for room_id in sorted({view.room_id for view in self._views.values()}):
            await self._update_offers(room_id)

    async def _update_offers(self, room_id: str, *, read: bool = True) -> None:
        """Send each seat of the room the agents it may add, when they differ from what it was last sent. `read` starts
        the reads of memberships still unknown or failed; the read that just ended asks for none, so a failure
        retries at the next trigger."""
        seats = self._recipients(room_id)
        if read:
            self._start_reads(room_id, seats)
        for peer_id in seats:
            view = self._view_of(peer_id, room_id)
            offers = self._offers_for(peer_id, room_id)
            if offers != view.offered:
                view.offered = offers
                await self._tell(room_id, [peer_id], _offers_frame(offers))

    def _offers_for(self, peer_id: PeerId, room_id: str) -> tuple[Offer, ...]:
        memberships = self._view_of(peer_id, room_id).memberships
        offers = []
        for live in self._live.by_person.values():
            group = self._adders.get(live.owner_subject)
            sponsor = self._sole_seat(room_id, live.owner_subject)
            if group is None or isinstance(sponsor, AgentRefusal) or memberships.get(group) is not Membership.MEMBER:
                continue
            place = self._places.get(live.person, room_id)
            if isinstance(place, Invited) and place.adder == peer_id:
                offers.append(Offer(live.key, live.name, pending=True))
            elif place is None and not self._is_busy(room_id, sponsor):
                offers.append(Offer(live.key, live.name, pending=False))
        return tuple(offers)

    def _start_reads(self, room_id: str, seats: Sequence[PeerId]) -> None:
        """Read the groups of each seat that could see an offer and whose membership is unknown or failed to read."""
        groups = {
            group
            for live in self._live.by_person.values()
            if (group := self._adders.get(live.owner_subject)) is not None
            and not isinstance(self._sole_seat(room_id, live.owner_subject), AgentRefusal)
        }
        for peer_id in seats:
            memberships = self._view_of(peer_id, room_id).memberships
            if any(memberships.get(group, Membership.FAILED) is Membership.FAILED for group in groups):
                self._start_read(peer_id, room_id)

    def _start_read(self, peer_id: PeerId, room_id: str) -> None:
        key = (peer_id, room_id)
        if key in self._reads:
            return
        read = asyncio.create_task(self._read_groups(peer_id, room_id, self._rooms.connection_of(peer_id)))
        self._reads[key] = read
        read.add_done_callback(lambda _: self._reads.pop(key, None))

    async def _read_groups(self, peer_id: PeerId, room_id: str, connection: HTTPConnection) -> None:
        groups = await self._groups_of(connection)
        if self._rooms.room_of(peer_id) != room_id:
            return
        memberships = self._view_of(peer_id, room_id).memberships
        for group in set(self._adders.values()):
            if groups is None:
                memberships[group] = Membership.FAILED
            else:
                memberships[group] = Membership.MEMBER if group in groups else Membership.NOT_MEMBER
        if groups is None:
            await logger.ainfo("agent_adders_read_failed", room_id=room_id)
        await self._update_offers(room_id, read=False)
