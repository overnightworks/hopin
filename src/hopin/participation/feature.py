import asyncio
import secrets
from functools import partial
from typing import TYPE_CHECKING, Final

import structlog

from hopin.errors import InvalidMessageError, RoomNotFoundError
from hopin.participation.chat_window import LineKind, WindowRead
from hopin.participation.figures import Figure, FigureSource, NamedFigure, figure_refusal
from hopin.participation.frames import (
    Offer,
    hearing_frame,
    invitation_ended_frame,
    joined_frame,
    left_frame,
    line_frame,
    offers_frame,
    refusal_frame,
    stop_frame,
    voice_frame,
)
from hopin.participation.live_agents import LiveAgents, TimerAction
from hopin.participation.memberships import Membership, SeatView, membership_in
from hopin.participation.participants import DialedAgent, DialOutcome, OwnAccount, Person
from hopin.participation.places import AbandonedInvitation, Invited, Places, PlaceStanding, PlaceState
from hopin.participation.speaking import Speaking, SpeakResult, StopResult
from hopin.participation.speech_in_windows import SpeechInWindows
from hopin.participation.text import clean_line, clean_name, one_line
from hopin.participation.vocabulary import (
    AGENT_ID_BYTES,
    AGENTS_PER_PROCESS,
    FEATURE_NAME,
    FIGURE_MODULE,
    SIGNAL_TYPES,
    SPEAK_FAILED_EVENT,
    AgentRefusal,
    HearingRefusal,
    InvitationOutcome,
    ParticipationSignal,
    SpeakFailure,
    SpeakStop,
)
from hopin.signaling.frames import JsonObject, PeerId

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence

    from starlette.requests import HTTPConnection

    from hopin.participation.hearing import Hearing, HearingHandle
    from hopin.participation.live_agents import LiveAgent
    from hopin.participation.places import Place
    from hopin.participation.speaking import QueuedSentence, Renderer
    from hopin.participation.voice import VoiceStore
    from hopin.signaling.features import CallRooms
    from hopin.signaling.frames import ForeignFrame
    from hopin.signaling.scheduler import Scheduler

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

AGENT_ADDED_EVENT: Final = "agent_invited"

type GroupsOf = Callable[[HTTPConnection], Awaitable[frozenset[str] | None]]
"""The groups the issuer holds for the session a connection carries now, or nothing when they cannot be read."""


GROUPS_READ_TASK_NAME = "participation-groups-read"


class Participation:
    """The agents in calls: dialed in by a person's own agent or added by a member of the owner's adders group,
    whichever seat sponsors them; each takes a place of the call like a person, and the process holds 32."""

    def __init__(
        self,
        rooms: CallRooms,
        scheduler: Scheduler,
        lease_seconds: float,
        invitation_seconds: float,
        on_changed: Callable[[Person], None],
        on_window_changed: Callable[[Person, str], None],
        wall_clock: Callable[[], float],
        adders: Mapping[str, str],
        groups_of: GroupsOf,
        listens_to_standing: Callable[[Person], bool],
        figure_sources: Collection[FigureSource],
        hearing: Hearing,
        voices: VoiceStore,
    ) -> None:
        self._rooms = rooms
        self._scheduler = scheduler
        self._lease_seconds = lease_seconds
        self._invitation_seconds = invitation_seconds
        self._on_changed = on_changed
        self._on_window_changed = on_window_changed
        self._wall_clock = wall_clock
        self._adders = adders
        self._groups_of = groups_of
        self._listens_to_standing = listens_to_standing
        self._figure_sources = figure_sources
        self._hearing = hearing
        self._places = Places()
        self.speech = SpeechInWindows(self._places, hearing, rooms, on_window_changed)
        self._speaking = Speaking(
            voices, scheduler, self._sentence_started, self.speech.sentence_ended, self._write_line
        )
        self._live = LiveAgents()
        self._departed: dict[PeerId, list[DialedAgent]] = {}
        self._abandoned: dict[PeerId, list[AbandonedInvitation]] = {}
        self._views: dict[PeerId, SeatView] = {}
        self._reads: dict[tuple[PeerId, str], asyncio.Task[None]] = {}

    @property
    def name(self) -> str:
        return FEATURE_NAME

    @property
    def browser_modules(self) -> tuple[str, ...]:
        return (FEATURE_NAME, FIGURE_MODULE) if self._figure_sources else (FEATURE_NAME,)

    @property
    def signal_types(self) -> Mapping[str, str]:
        return SIGNAL_TYPES

    async def dial_in(self, person: Person, room_id: str, name: str) -> DialOutcome:
        """Bind the person's agent to the person's one seat in the room; a repeat dial-in returns the same agent."""
        agent_name = clean_name(name)
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
        agent = DialedAgent(secrets.token_hex(AGENT_ID_BYTES), agent_name, person, sponsor, room_id)
        if not self._takes_place(agent):
            return AgentRefusal.BUSY
        self._places.put(person, room_id, agent)
        self._hearing.joined(agent, brought_by_sponsor=True)
        self._on_changed(person)
        await self._announce_joined(agent, self._rooms.members(room_id), self._rooms.name_of(sponsor))
        await self._tell_hearing(agent)
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

    async def say(self, person: Person, room_id: str, text: str) -> AgentRefusal | None:
        """Show the person's agent's line to every seat in the room; the refusal, or nothing when it was sent."""
        agent = self._places.get(person, room_id)
        if not isinstance(agent, DialedAgent):
            return AgentRefusal.GONE
        line = clean_line(text)
        if line is None:
            return AgentRefusal.BAD_LINE
        if not self._places.take_line(person, room_id, self._scheduler.now()):
            return AgentRefusal.TOO_FAST
        await self._write_line(agent, line)
        return None

    async def speak(self, person: Person, room_id: str, text: str, render: Renderer | None) -> SpeakResult:
        """Queue the person's agent's sentence behind its earlier ones once `render` made its sound, and answer how long
        it plays when the sentence starts, not when it is queued; the refusal when the agent holds no active place
        there or the sentence cannot be spoken: no text, no renderer, past the agent's line rate or waiting queue, no
        sound, or an end of its speaking meanwhile."""
        agent = self._active_agent(person, room_id)
        if isinstance(agent, AgentRefusal):
            return agent
        sentence = clean_line(one_line(text))
        if sentence is None or render is None:
            return await self._unavailable(SpeakFailure.NO_TEXT if sentence is None else SpeakFailure.SPEAKING_OFF)
        failure = self._no_room_to_speak(agent)
        if failure is not None:
            return await self._unavailable(failure)
        return await self._speaking.speak(agent, sentence, self.speech.watching_voice(agent, render))

    async def stop_speaking(self, person: Person, room_id: str) -> StopResult:
        """Cut the sentence the person's agent plays in every seat and drop its others; the text of the cut sentence's
        line, or why nothing was cut: the agent holds no active place there, or no sentence of it plays."""
        agent = self._active_agent(person, room_id)
        if isinstance(agent, AgentRefusal):
            return agent
        cut = self._speaking.stop(agent)
        if cut is None:
            return SpeakStop.NOTHING_PLAYING
        await self._tell(room_id, self._recipients(room_id), stop_frame(agent))
        return cut

    def holds_seat(self, account_id: str, room_id: str) -> bool:
        """Whether the account holds a seat in the room now, a parked one included."""
        return bool(self._rooms.seats_in(room_id, account_id))

    def window_rooms_of(self, person: Person) -> tuple[str, ...]:
        """The rooms where the person holds a joined agent, whose chat windows it may read."""
        return self._places.window_rooms_of(person)

    def read_window(self, person: Person, room_id: str) -> WindowRead | None:
        """The chat window of the person's joined agent in the room, or nothing when it holds none there."""
        window = self._places.window_of(person, room_id)
        return None if window is None else window.read()

    def standings_of(self, person: Person) -> tuple[PlaceStanding, ...]:
        """Every place the person holds as it reads now; a place it was removed from is absent."""
        return tuple(self._standing_in(room_id, place) for room_id, place in self._places.of_person(person))

    def open_hearing(
        self, account: Person, seat: PeerId, holds_token: Callable[[Person], bool]
    ) -> HearingHandle | HearingRefusal:
        """The sound session of a seat the account holds in a room, served by an agent that hears it and ending with
        that agent's hearing; refused for a seat the account lacks, one waiting to resume or one nobody hears."""
        room_id = self._rooms.room_of(seat)
        if room_id is None or seat not in self._rooms.seats_in(room_id, account.subject):
            return HearingRefusal.NO_SEAT
        if self._rooms.is_parked(seat):
            return HearingRefusal.SEAT_AWAY
        return self._hearing.open(account, room_id, seat, holds_token)

    def heard(self, seat: PeerId, text: str, began_at: float) -> None:
        """Add a finished sentence the seat said, which began at `began_at` on the scheduler's clock, to the window of
        each joined agent that hears the seat, under the name the call shows for the seat."""
        room_id = self._rooms.room_of(seat)
        if room_id is None:
            return
        self.speech.person_said(room_id, seat, text, began_at)

    def is_live_agent(self, person: Person) -> bool:
        """Whether the person is an agent with an account of its own whose lease is registered."""
        return person in self._live.by_person

    def figures_named(self, sha256: str) -> tuple[NamedFigure, ...]:
        """The URLs the joined agents name for the digest."""
        return tuple(
            NamedFigure(agent.room_id, agent.figure.url)
            for agent in self._places.joined()
            if agent.figure is not None and agent.figure.sha256 == sha256
        )

    async def accept(
        self,
        person: Person,
        room_id: str,
        invitation_id: str,
        epoch: int,
        figure: Figure | None,
        voice: str | None = None,
    ) -> DialOutcome:
        """Turn the agent's open invitation into its tile, bound to the owner's seat the invitation names; the tile
        shows the figure only when an allowed source holds it, and the agent keeps `voice` for its sentences."""
        live = self._live.by_person.get(person)
        invitation = self._places.standing_invitation(person, room_id, invitation_id, epoch, self._scheduler.now())
        if live is None or invitation is None:
            return AgentRefusal.STALE_INVITATION
        if self._rooms.seats_in(room_id, live.owner_subject) != [invitation.sponsor]:
            return AgentRefusal.TWO_SEATS
        if self._rooms.is_parked(invitation.sponsor):
            return AgentRefusal.SPONSOR_AWAY
        refusal = None if figure is None else figure_refusal(self._figure_sources, figure.url)
        agent = DialedAgent(
            secrets.token_hex(AGENT_ID_BYTES),
            live.name,
            person,
            invitation.sponsor,
            room_id,
            figure if refusal is None else None,
            voice,
        )
        if not self._takes_place(agent):
            return AgentRefusal.BUSY
        self._places.put(person, room_id, agent)
        self._hearing.joined(agent, brought_by_sponsor=invitation.adder == invitation.sponsor)
        self._on_changed(person)
        await logger.ainfo("agent_invitation_ended", room_id=room_id, reason="accepted")
        if refusal is not None:
            await logger.ainfo("agent_figure_refused", room_id=room_id, reason=refusal.value)
        await self._announce_joined(agent, self._rooms.members(room_id), self._rooms.name_of(invitation.sponsor))
        await self._tell_hearing(agent)
        await self._update_offers(room_id)
        return agent

    async def decline(
        self, person: Person, room_id: str, invitation_id: str, epoch: int, outcome: InvitationOutcome
    ) -> AgentRefusal | None:
        """End the agent's standing invitation as its expiry does and tell the seat that made it `outcome`; the refusal,
        or nothing when it ended. A decline seats nothing, so it needs no lease."""
        invitation = self._places.standing_invitation(person, room_id, invitation_id, epoch, self._scheduler.now())
        if invitation is None:
            return AgentRefusal.STALE_INVITATION
        await self._end_invitation(person, room_id, invitation, outcome, reason="declined")
        return None

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

    async def listening_changed(self) -> None:
        """Some agent's listen on its standing started or ended: the offers of every room follow it, as a lease's start
        and end do."""
        await self._update_every_room_offers()

    async def on_frame(self, peer_id: PeerId, frame: ForeignFrame) -> bool:
        match frame.signal_type:
            case ParticipationSignal.AGENT_REMOVE:
                await self._on_remove(peer_id, frame)
            case ParticipationSignal.AGENT_ADD:
                await self._on_add(peer_id, frame)
            case ParticipationSignal.AGENT_HEARING if self._hearing.enabled:
                await self._on_hearing(peer_id, frame)
            case _:
                return False
        return True

    async def seat_joined(self, peer_id: PeerId) -> None:
        """Show a seat that just joined the agents already in its room, and the agents it may add."""
        room_id = self._rooms.room_of(peer_id)
        if room_id is None:  # pragma: no cover - a seat is in its room when it is announced
            return
        self._views[peer_id] = SeatView(room_id)
        for agent in [agent for agent in self._places.joined() if agent.room_id == room_id]:
            if self._in_call(agent):
                await self._announce_joined(agent, [peer_id], self._rooms.name_of(agent.sponsor))
        await self._update_offers(room_id)

    def chat_said(self, peer_id: PeerId, room_id: str, message: str) -> None:
        """Add a person's line to the window of every agent joined in its room, under the name the call shows."""
        self.speech.line_said(room_id, LineKind.PERSON, self._rooms.name_of(peer_id), message, sayer=None)

    def seat_changed(self, peer_id: PeerId) -> None:
        """A seat that left its room takes its places out of the state at once and holds its agents for `seat_left`;
        a seat that left, parked or resumed tells the agents it sponsors."""
        bound = [
            (person, room_id, place) for person, room_id, place in self._places.entries() if place.sponsor == peer_id
        ]
        if self._rooms.room_of(peer_id) is None:
            self._take_out_places_of_departed(peer_id, bound)
        elif self._rooms.is_parked(peer_id):
            self._hearing.end_sound(peer_id)
            for _, _, place in bound:
                if isinstance(place, DialedAgent):
                    self._speaking.end(place)
        for person in {person for person, _, _ in bound}:
            self._on_changed(person)

    def _take_out_places_of_departed(self, peer_id: PeerId, bound: list[tuple[Person, str, Place]]) -> None:
        view = self._views.pop(peer_id, None)
        invitations = [
            AbandonedInvitation(room_id, self._live.by_person[person].key, place.adder)
            for person, room_id, place in bound
            if isinstance(place, Invited)
        ]
        for person, room_id, _ in bound:
            self._places.remove(person, room_id)
        if invitations:
            self._abandoned[peer_id] = invitations
        joined = [place for _, _, place in bound if isinstance(place, DialedAgent)]
        for agent in joined:
            self._agent_ended(agent)
        if view is not None:
            self._hearing.seat_ended(view.room_id, peer_id)
        if joined:
            self._departed[peer_id] = joined

    async def seat_left(self, peer_id: PeerId, *, room_id: str, name: str) -> None:
        """Announce the tiles of the agents the seat took with it and the agents the others may add now, then tell the
        seats that invited the owner's agents that the owner is away."""
        del name
        for agent in self._departed.pop(peer_id, []):
            await self._announce_removed(agent, self._recipients(agent.room_id))
        await self._update_offers(room_id)
        for invitation in self._abandoned.pop(peer_id, []):
            await self._tell(
                invitation.room_id,
                [invitation.adder],
                invitation_ended_frame(invitation.agent_key, InvitationOutcome.OWNER_ABSENT),
            )

    def room_ended(self, room_id: str) -> None:
        """Drop the room's places and seats; the room's seats hear the end itself."""
        self._forget_seats_of_ended_room(room_id)
        holders = {person for person, held_room, _ in self._places.entries() if held_room == room_id}
        self._hearing.room_ended(room_id)
        ended = [agent for agent in self._places.joined() if agent.room_id == room_id]
        self._places.drop_room(room_id)
        for agent in ended:
            self._speaking.end(agent)
        for person in holders:
            self._on_changed(person)

    def _forget_seats_of_ended_room(self, room_id: str) -> None:
        for sponsor in [sponsor for sponsor, gone in self._departed.items() if gone[0].room_id == room_id]:
            del self._departed[sponsor]
        for sponsor in [sponsor for sponsor, gone in self._abandoned.items() if gone[0].room_id == room_id]:
            del self._abandoned[sponsor]
        for peer_id in [peer_id for peer_id, view in self._views.items() if view.room_id == room_id]:
            del self._views[peer_id]

    async def shut_down(self) -> None:
        reads = list(self._reads.values())
        for read in reads:
            read.cancel()
        await asyncio.gather(*reads, return_exceptions=True)
        for room_id in {agent.room_id for agent in self._places.joined()}:
            self._hearing.room_ended(room_id)
        ended = self._places.joined()
        self._places.clear()
        for agent in ended:
            self._speaking.end(agent)
        self._departed.clear()
        self._abandoned.clear()
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
            await self._rooms.send(peer_id, refusal_frame(AgentRefusal.NOT_SPONSOR_OR_HOST))
            return
        await self._remove(agent)

    async def _on_hearing(self, peer_id: PeerId, frame: ForeignFrame) -> None:
        agent_id = frame.data.get("agent_id")
        on = frame.data.get("on")
        if not isinstance(agent_id, str) or not isinstance(on, bool):
            raise InvalidMessageError("A hearing switch needs the agent's id and on or off")
        room_id = self._rooms.room_of(peer_id)
        agent = next(
            (agent for agent in self._places.joined() if agent.agent_id == agent_id and agent.room_id == room_id), None
        )
        if agent is None:
            await self._rooms.send(peer_id, refusal_frame(AgentRefusal.GONE))
        elif agent.sponsor != peer_id:
            await self._rooms.send(peer_id, refusal_frame(AgentRefusal.NOT_SPONSOR))
        else:
            self._hearing.switch(agent, on=on)
            self.speech.hearing_switched(agent, on=on)
            await self._tell_hearing(agent)

    async def _on_add(self, peer_id: PeerId, frame: ForeignFrame) -> None:
        agent_key = frame.data.get("agent_key")
        if not isinstance(agent_key, str):
            raise InvalidMessageError("An add needs the agent's key")
        room_id = self._rooms.room_of(peer_id)
        live = None if room_id is None else self._live.by_key(agent_key)
        if room_id is None or live is None:
            await self._rooms.send(peer_id, refusal_frame(AgentRefusal.GONE))
            return
        refusal = self._refusal_of_add(peer_id, room_id, live, membership_is_fresh=False)
        if refusal is not None:
            await self._rooms.send(peer_id, refusal_frame(refusal))
            return
        await self._add_after_a_fresh_read(peer_id, room_id, live)

    async def _add_after_a_fresh_read(self, peer_id: PeerId, room_id: str, live: LiveAgent) -> None:
        [sponsor] = self._rooms.seats_in(room_id, live.owner_subject)  # the checks before it found exactly one
        groups = await self._groups_of(self._rooms.connection_of(peer_id))
        if self._rooms.room_of(peer_id) != room_id:
            return
        group = self._adders[live.owner_subject]
        memberships = self._view_of(peer_id, room_id).memberships
        memberships[group] = membership_in(groups, group)
        refusal: AgentRefusal | None
        if memberships[group] is Membership.FAILED:
            refusal = AgentRefusal.UNAVAILABLE
        else:
            refusal = self._refusal_of_add_now(peer_id, room_id, live, sponsor)
        if refusal is None:
            await self._invite(peer_id, room_id, live, sponsor)
        await self._update_offers(room_id)
        if refusal is not None:
            # After the offers: every offers frame a seat gets after a refusal is a later one, which clears it.
            await self._rooms.send(peer_id, refusal_frame(refusal))

    def _refusal_of_add_now(
        self, peer_id: PeerId, room_id: str, live: LiveAgent, sponsor: PeerId
    ) -> AgentRefusal | None:
        """The add as it stands after the fresh read: the same registration and the same single seat of the owner."""
        current = self._live.by_person.get(live.person)
        if current is None or current.generation != live.generation:
            return AgentRefusal.GONE
        refusal = self._refusal_of_add(peer_id, room_id, live, membership_is_fresh=True)
        if refusal is None and self._rooms.seats_in(room_id, live.owner_subject) != [sponsor]:
            return AgentRefusal.NO_SEAT
        return refusal

    def _refusal_of_add(
        self, peer_id: PeerId, room_id: str, live: LiveAgent, *, membership_is_fresh: bool
    ) -> AgentRefusal | None:
        """The refusals an add meets; a membership read before this add decides nothing, only the fresh one does."""
        sponsor = self._sole_seat(room_id, live.owner_subject)
        if isinstance(sponsor, AgentRefusal):
            return sponsor
        if self._rooms.is_parked(sponsor):
            return AgentRefusal.SPONSOR_AWAY
        group = self._adders.get(live.owner_subject)
        if group is None or (
            membership_is_fresh and self._view_of(peer_id, room_id).memberships.get(group) is Membership.NOT_MEMBER
        ):
            return AgentRefusal.NOT_ADDER
        if self._places.get(live.person, room_id) is not None:
            return AgentRefusal.ALREADY_THERE
        if self._is_busy(room_id):
            return AgentRefusal.BUSY
        return None

    async def _invite(self, adder: PeerId, room_id: str, live: LiveAgent, sponsor: PeerId) -> None:
        invitation = Invited(
            invitation_id=secrets.token_hex(AGENT_ID_BYTES),
            epoch=self._places.epoch_of(live.person, room_id),
            expires_at=self._wall_clock() + self._invitation_seconds,
            deadline=self._scheduler.now() + self._invitation_seconds,
            sponsor=sponsor,
            adder=adder,
        )
        self._places.put(live.person, room_id, invitation)
        self._scheduler.call_later(
            self._invitation_seconds, partial(self._invitation_timer, live.person, room_id, invitation.invitation_id)
        )
        self._on_changed(live.person)
        await logger.ainfo(AGENT_ADDED_EVENT, room_id=room_id)

    async def _invitation_timer(self, person: Person, room_id: str, invitation_id: str) -> None:
        place = self._places.get(person, room_id)
        if isinstance(place, Invited) and place.invitation_id == invitation_id:
            await self._end_invitation(person, room_id, place, InvitationOutcome.NO_ANSWER, reason="expired")

    async def _end_invitation(
        self, person: Person, room_id: str, invitation: Invited, outcome: InvitationOutcome, *, reason: str
    ) -> None:
        agent_key = self._live.by_person[person].key  # read before the awaits: the lease may end during them
        self._places.end_invitation(person, room_id)
        self._on_changed(person)
        await logger.ainfo("agent_invitation_ended", room_id=room_id, reason=reason)
        await self._update_offers(room_id)
        # After the offers, which list the agent as available again: the outcome then marks that row.
        await self._tell(room_id, [invitation.adder], invitation_ended_frame(agent_key, outcome))

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
        """End every place of the agent in one synchronous step, then signal the standing and tell the rooms."""
        departures: list[tuple[DialedAgent, list[PeerId]]] = []
        for room_id, place in self._places.of_person(ended.person):
            self._places.remove(ended.person, room_id)
            if isinstance(place, DialedAgent):
                self._agent_ended(place)
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

    async def _write_line(self, agent: DialedAgent, line: str) -> None:
        """Show the agent's written line to every joined agent's window and every seat in its room."""
        self.speech.line_said(agent.room_id, LineKind.AGENT, agent.name, line, sayer=agent.person)
        await self._tell(agent.room_id, self._recipients(agent.room_id), line_frame(agent, line))

    def _active_agent(self, person: Person, room_id: str) -> DialedAgent | AgentRefusal:
        """The person's agent in the room when it is joined and its sponsor's seat is connected, else why it is not."""
        agent = self._places.get(person, room_id)
        if not isinstance(agent, DialedAgent):
            return AgentRefusal.GONE
        return AgentRefusal.SPONSOR_AWAY if self._rooms.is_parked(agent.sponsor) else agent

    def _no_room_to_speak(self, agent: DialedAgent) -> SpeakFailure | None:
        """Why the agent may not queue one more sentence, or nothing when it may and takes a line slot for it."""
        if self._speaking.is_full(agent):
            return SpeakFailure.QUEUE_FULL
        if not self._places.take_line(agent.person, agent.room_id, self._scheduler.now()):
            return SpeakFailure.TOO_FAST
        return None

    async def _unavailable(self, failure: SpeakFailure) -> AgentRefusal:
        """Answer `unavailable` for a sentence never rendered, its closed cause logged and nothing else."""
        await logger.ainfo(SPEAK_FAILED_EVENT, outcome=failure.value)
        return AgentRefusal.UNAVAILABLE

    def _agent_ended(self, agent: DialedAgent) -> None:
        """The agent's removal, leave or lease end: it frees its place and hears and speaks nothing more."""
        self._rooms.release_agent_place(agent.room_id, agent.agent_id)
        self._hearing.agent_ended(agent)
        self._speaking.end(agent)

    async def _sentence_started(self, agent: DialedAgent, sentence: QueuedSentence) -> None:
        """A sentence of the agent begins to play: it enters the call's chat as the agent's line, and every seat is told
        its voice."""
        self.speech.sentence_started(agent)
        recipients = self._recipients(agent.room_id)
        await self._tell(agent.room_id, recipients, line_frame(agent, sentence.text))
        for peer_id in recipients:
            if not self._speaking.is_playing(agent, sentence):
                return
            await self._tell(agent.room_id, [peer_id], voice_frame(agent, sentence.voice.id))

    def _sole_seat(self, room_id: str, owner_subject: str) -> PeerId | AgentRefusal:
        seats = self._rooms.seats_in(room_id, owner_subject)
        if not seats:
            return AgentRefusal.NO_SEAT
        if len(seats) > 1:
            return AgentRefusal.TWO_SEATS
        return seats[0]

    def _is_busy(self, room_id: str) -> bool:
        """The call has no place left for an agent, or the process holds its 32."""
        return len(self._places.joined()) >= AGENTS_PER_PROCESS or not self._rooms.has_free_place(room_id)

    def _takes_place(self, agent: DialedAgent) -> bool:
        if self._is_busy(agent.room_id):
            return False
        self._rooms.take_agent_place(agent.room_id, agent.agent_id)
        return True

    def _in_call(self, agent: DialedAgent) -> bool:
        """The agent holds its place, so its seat has not taken it away."""
        return self._places.get(agent.person, agent.room_id) == agent

    def _view_of(self, peer_id: PeerId, room_id: str) -> SeatView:
        return self._views.setdefault(peer_id, SeatView(room_id))

    def _recipients(self, room_id: str) -> list[PeerId]:
        """The room's seats now, none once the room has ended."""
        try:
            return [PeerId(peer_id) for peer_id in self._rooms.members(room_id)]
        except RoomNotFoundError:
            return []

    async def _remove(self, agent: DialedAgent) -> None:
        recipients = self._recipients(agent.room_id)
        self._places.remove(agent.person, agent.room_id)
        self._agent_ended(agent)
        self._on_changed(agent.person)
        await self._announce_removed(agent, recipients)
        await self._update_offers(agent.room_id)

    async def _announce_removed(self, agent: DialedAgent, recipients: Sequence[str]) -> None:
        await self._tell(agent.room_id, recipients, left_frame(agent))
        await logger.ainfo("agent_left", room_id=agent.room_id, agent_id=agent.agent_id)

    async def _announce_joined(self, agent: DialedAgent, peer_ids: Sequence[str], sponsor_name: str) -> None:
        """Tell the seats of the agent's tile, each only while the agent is still in the call."""
        frame = joined_frame(agent, sponsor_name)
        for peer_id in peer_ids:
            if not self._in_call(agent):
                return
            await self._tell(agent.room_id, [peer_id], frame)

    async def _tell_hearing(self, agent: DialedAgent) -> None:
        """Tell the agent's sponsor the server's record of the agent's switch, while the agent is in the call and the
        call lets agents hear."""
        on = self._hearing.switch_of(agent)
        if on is not None and self._in_call(agent):
            await self._tell(agent.room_id, [agent.sponsor], hearing_frame(agent, on=on))

    async def _tell(self, room_id: str, peer_ids: Sequence[str], frame: JsonObject) -> None:
        """Send to each seat that still sits in the room: a room that has ended gets nothing more."""
        for peer_id in peer_ids:
            if self._rooms.room_of(PeerId(peer_id)) == room_id:
                await self._rooms.send(peer_id, frame)

    async def _update_every_room_offers(self) -> None:
        for room_id in sorted({view.room_id for view in self._views.values()}):
            await self._update_offers(room_id)

    async def _update_offers(self, room_id: str, *, read: bool = True) -> None:
        """Send each seat the agents it may add, whether its control shows and whether no place is free for an agent,
        when they differ from what it was last sent. `read` starts the reads of memberships still unknown or failed;
        the read that just ended asks for none, so a failure retries at the next trigger."""
        seats = self._recipients(room_id)
        if not seats:  # the room has ended
            return
        if read:
            self._start_reads(room_id, seats)
        full = self._is_busy(room_id)
        for peer_id in seats:
            view = self._view_of(peer_id, room_id)
            offers = self._offers_for(peer_id, room_id)
            shown = self._control_shown_to(peer_id, room_id, offers)
            if (offers, shown, full) != (view.offered, view.shown, view.full):
                view.offered = offers
                view.shown = shown
                view.full = full
                await self._tell(room_id, [peer_id], offers_frame(offers, shown=shown, full=full))

    def _control_shown_to(self, peer_id: PeerId, room_id: str, offers: Sequence[Offer]) -> bool:
        """The control shows to a seat with an offer, to the call's host and to a member of an adders group, even with
        no agent to list; it never waits on a fresh read."""
        if offers:
            return True
        memberships = self._view_of(peer_id, room_id).memberships
        return self._rooms.is_host(peer_id) or any(
            memberships.get(group) is Membership.MEMBER for group in self._adders.values()
        )

    def _offers_for(self, peer_id: PeerId, room_id: str) -> tuple[Offer, ...]:
        """The agents a seat may add: each with a lease that runs and a listen on its standing that is open, or ended
        within its allowance."""
        memberships = self._view_of(peer_id, room_id).memberships
        offers = []
        for live in self._live.by_person.values():
            group = self._adders.get(live.owner_subject)
            sponsor = self._sole_seat(room_id, live.owner_subject)
            if (
                group is None
                or isinstance(sponsor, AgentRefusal)
                or memberships.get(group) is not Membership.MEMBER
                or not self._listens_to_standing(live.person)
            ):
                continue
            place = self._places.get(live.person, room_id)
            if isinstance(place, Invited) and place.adder == peer_id:
                offers.append(Offer(live.key, live.name, pending=True))
            elif place is None and not self._is_busy(room_id):
                offers.append(Offer(live.key, live.name, pending=False))
        return tuple(offers)

    def _start_reads(self, room_id: str, seats: Sequence[PeerId]) -> None:
        """Read the groups of each seat whose membership in an adders group is unknown or failed to read."""
        groups = set(self._adders.values())
        for peer_id in seats:
            memberships = self._view_of(peer_id, room_id).memberships
            if any(memberships.get(group, Membership.FAILED) is Membership.FAILED for group in groups):
                self._start_read(peer_id, room_id)

    def _start_read(self, peer_id: PeerId, room_id: str) -> None:
        key = (peer_id, room_id)
        if key in self._reads:
            return
        read = asyncio.create_task(
            self._read_groups(peer_id, room_id, self._rooms.connection_of(peer_id)), name=GROUPS_READ_TASK_NAME
        )
        self._reads[key] = read
        read.add_done_callback(lambda _: self._reads.pop(key, None))

    async def _read_groups(self, peer_id: PeerId, room_id: str, connection: HTTPConnection) -> None:
        groups = await self._groups_of(connection)
        if self._rooms.room_of(peer_id) != room_id:
            return
        memberships = self._view_of(peer_id, room_id).memberships
        for group in set(self._adders.values()):
            memberships[group] = membership_in(groups, group)
        if groups is None:
            await logger.ainfo("agent_adders_read_failed", room_id=room_id)
        await self._update_offers(room_id, read=False)
