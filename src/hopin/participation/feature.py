import secrets
from functools import partial
from typing import TYPE_CHECKING, Final, cast

import structlog

from hopin.errors import InvalidMessageError
from hopin.participation.chat_window import LineKind, WindowRead
from hopin.participation.figures import Figure, FigureSource, judge_figure
from hopin.participation.frames import (
    AgentInvitationEndedFrame,
    AgentLeftFrame,
    AgentLineFrame,
    AgentOffersFrame,
    AgentRefusedFrame,
    AgentStopFrame,
    AgentVoiceFrame,
    Offer,
    ParticipationFrame,
    joined_frame,
)
from hopin.participation.group_reads import GroupReads, GroupsOf, GroupsRead
from hopin.participation.live_agents import LiveAgents
from hopin.participation.memberships import Membership, SeatView, membership_in
from hopin.participation.participants import DialedAgent, DialOutcome, OwnAccount, Person
from hopin.participation.places import (
    AbandonedInvitation,
    InvitationStanding,
    Invited,
    Places,
    PlaceStanding,
    PlaceState,
    invitation_key,
    invitation_of,
)
from hopin.participation.seeing import Seeing
from hopin.participation.shown_figures import ShownFigures
from hopin.participation.speaking import Speaking, SpeakResult, StopResult
from hopin.participation.speech_in_windows import SpeechInWindows
from hopin.participation.switches import Switches
from hopin.participation.text import clean_line, clean_name, one_line
from hopin.participation.vocabulary import (
    AGENT_ID_BYTES,
    FEATURE_NAME,
    FIGURE_MODULE,
    SIGNAL_TYPES,
    AgentRefusal,
    HearingRefusal,
    InvitationOutcome,
    LookRefusal,
    ParticipationSignal,
    SpeakFailure,
    SpeakStop,
)
from hopin.rooms.reference import room_reference
from hopin.signaling.frames import PeerId
from hopin.signaling.replay import Supersedes
from hopin.signaling.scheduler import DeadlineKind

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping, Sequence

    from hopin.participation.figures import FigureVerdict
    from hopin.participation.hearing import Hearing, HearingHandle
    from hopin.participation.live_agents import LiveAgent
    from hopin.participation.looking import Picture
    from hopin.participation.places import Place
    from hopin.participation.speaking import QueuedSentence, Renderer, VoicesOffered
    from hopin.participation.voice import VoiceStore
    from hopin.signaling.features import CallRooms
    from hopin.signaling.frames import ForeignFrame
    from hopin.signaling.scheduler import Deadlines
    from hopin.signaling.transition import Committed, LiveSeats, RoomView, Transition, Work, WorkRoom

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

INVITATION_CLOSED_EVENT: Final = "agent_invitation.closed"
"""The log event of an invitation that ends, with the reason it ended."""


class Participation:
    """The agents in calls: dialed in by a person's own agent or added by a member of the owner's adders group,
    whichever seat sponsors them; each takes a place of the call like a person."""

    def __init__(
        self,
        rooms: CallRooms,
        deadlines: Deadlines,
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
        self._transitions = rooms.transitions_for(self)
        self._deadlines = deadlines
        deadlines.register(DeadlineKind.INVITATION, self._invitation_timer)
        deadlines.register(DeadlineKind.LEASE, self._lease_timer)
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
        self.speech = SpeechInWindows(self._places, hearing, rooms, on_window_changed, deadlines)
        self._speaking = Speaking(
            voices, deadlines, self._sentence_started, self.speech.sentence_ended, self._write_line
        )
        self.figures = ShownFigures(self._places, self._active_agent, self._tell, self._recipients)
        self._seeing = Seeing(hearing, self._places, deadlines, self._tell, self._recipients)
        self._switches = Switches(self._places, hearing, self.speech, self._seeing)
        self._live = LiveAgents()
        self._departed: dict[PeerId, list[DialedAgent]] = {}
        self._abandoned: dict[PeerId, list[AbandonedInvitation]] = {}
        self._views: dict[PeerId, SeatView] = {}
        self._group_reads = GroupReads()

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
        outcome, seated = await self._transitions.apply(room_id, partial(self._dial_in, person, agent_name))
        if seated is not None:
            await logger.ainfo("agent.dialed_in", room_ref=room_reference(room_id), agent_id=seated.agent_id)
            await self._update_offers(room_id)
        return outcome

    def _dial_in(
        self, person: Person, agent_name: str, view: RoomView, transition: Transition
    ) -> tuple[DialOutcome, DialedAgent | None]:
        """The dial-in's outcome, and the agent when it seated a new one."""
        seats = view.seats_in(person.subject)
        if not seats:
            return AgentRefusal.NO_SEAT, None
        if len(seats) > 1:
            return AgentRefusal.TWO_SEATS, None
        [sponsor] = seats
        if view.is_parked(sponsor):
            return AgentRefusal.SPONSOR_AWAY, None
        room_id = cast("str", view.room_id)  # the person's seat sits in it
        existing = self._places.get(person, room_id)
        if isinstance(existing, DialedAgent) and existing.sponsor == sponsor:
            return existing, None
        agent = DialedAgent(secrets.token_hex(AGENT_ID_BYTES), agent_name, person, sponsor, room_id)
        if not view.has_free_place:
            return AgentRefusal.BUSY, None
        self._seat(view, agent, transition, brought_by_sponsor=True)
        return agent, agent

    def _seat(self, view: RoomView, agent: DialedAgent, transition: Transition, *, brought_by_sponsor: bool) -> None:
        """Give the agent its place and tile, and tell its sponsor its switches."""
        transition.take_agent_place(agent.agent_id)
        self._places.put(agent.person, agent.room_id, agent)
        self._hearing.joined(agent, brought_by_sponsor=brought_by_sponsor)
        self._on_changed(agent.person)
        self._announce_joined(view, agent, view.members, transition)
        self._switches.tell(view, agent, transition)

    async def leave(self, person: Person, room_id: str) -> AgentRefusal | None:
        """Take the person's agent out of the room's call; the refusal, or nothing when it is out. An open invitation
        is no place to leave."""
        refusal = await self._transitions.apply(room_id, partial(self._leave, person))
        if refusal is None:
            await self._update_offers(room_id)
        return refusal

    def _leave(self, person: Person, view: RoomView, transition: Transition) -> AgentRefusal | None:
        agent = None if view.room_id is None else self._places.get(person, view.room_id)
        if not isinstance(agent, DialedAgent):
            return AgentRefusal.GONE
        if view.is_parked(agent.sponsor):
            return AgentRefusal.SPONSOR_AWAY
        self._remove(view, agent, transition)
        return None

    async def say(self, person: Person, room_id: str, text: str) -> AgentRefusal | None:
        """Show the person's agent's line to every seat in the room; the refusal, or nothing when it was sent."""
        agent = self._places.get(person, room_id)
        if not isinstance(agent, DialedAgent):
            return AgentRefusal.GONE
        line = clean_line(text)
        if line is None:
            return AgentRefusal.BAD_LINE
        await self._write_line(agent, line)
        return None

    async def speak(
        self, person: Person, room_id: str, text: str, render: Renderer | None, offered: VoicesOffered | None = None
    ) -> SpeakResult:
        """Queue the person's agent's sentence behind its earlier ones once `render` made its sound, and answer that
        it plays when the sentence starts, not when it is queued; the refusal when the agent holds no active place
        there or the sentence cannot be spoken: no text, no renderer, past the agent's waiting queue, or an
        end of its speaking meanwhile; a sound that could not be made is written as a line instead, and `offered` then
        says when the voice is back, which turns the window's voice available without a sentence."""
        agent = self._active_agent(person, room_id)
        if isinstance(agent, AgentRefusal):
            return agent
        sentence = clean_line(one_line(text))
        if sentence is None or render is None:
            return self._unavailable(
                agent, text, SpeakFailure.NO_TEXT if sentence is None else SpeakFailure.SPEAKING_OFF
            )
        failure = self._no_room_to_speak(agent)
        if failure is not None:
            return self._unavailable(agent, text, failure)
        return await self._speaking.speak(agent, sentence, self.speech.watching_voice(agent, render, offered))

    async def stop_speaking(self, person: Person, room_id: str) -> StopResult:
        """Cut the sentence the person's agent plays in every seat and drop its others; the text of the cut sentence's
        line, or why nothing was cut: the agent holds no active place there, or no sentence of it plays."""
        agent = self._active_agent(person, room_id)
        if isinstance(agent, AgentRefusal):
            return agent
        cut = self._speaking.stop(agent)
        if cut is None:
            return SpeakStop.NOTHING_PLAYING
        await self._tell(room_id, self._recipients(room_id), AgentStopFrame(agent.agent_id))
        return cut

    async def look(self, person: Person, room_id: str) -> Picture:
        """The JPEG of the camera of the person's agent's sponsor, which the sponsor's seeing switch lets it take; why
        there is none otherwise."""
        agent = self._active_agent(person, room_id)
        if isinstance(agent, AgentRefusal):
            return LookRefusal.NOT_ACTIVE
        return await self._seeing.look(agent)

    def holds_seat(self, account_id: str, room_id: str) -> bool:
        """Whether the account holds a seat in the room now, a parked one included."""
        return bool(self._transitions.view(room_id).seats_in(account_id))

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

    def heard(self, seat: PeerId, text: str, began_at: float, item_id: str) -> None:
        """Add a finished sentence the seat said, which began at `began_at` on the deadlines' clock and which the
        speech service knows as `item_id`, to the window of each joined agent that hears the seat, under the name the
        call shows for the seat."""
        room_id = self._rooms.room_of(seat)
        if room_id is None:
            return
        self.speech.person_said(room_id, seat, text, began_at, item_id)

    def is_live_agent(self, person: Person) -> bool:
        """Whether the person is an agent with an account of its own whose lease is registered."""
        return person in self._live.by_person

    async def accept(
        self,
        person: Person,
        room_id: str,
        invitation_id: str,
        epoch: int,
        figure: Figure | None,
        voices: tuple[str, ...] = (),
    ) -> DialOutcome:
        """Turn the agent's open invitation into its tile, bound to the owner's seat the invitation names; the tile
        shows the figure only when an allowed source holds it, and the agent keeps `voices` for its sentences."""
        verdict = judge_figure(self._figure_sources, figure)
        outcome = await self._transitions.apply(
            room_id, partial(self._accept, person, invitation_id, epoch, verdict, voices)
        )
        if not isinstance(outcome, AgentRefusal):
            await logger.ainfo(INVITATION_CLOSED_EVENT, room_ref=room_reference(room_id), reason="accepted")
            if verdict.refusal is not None:
                await logger.ainfo(
                    "agent_figure.refused", room_ref=room_reference(room_id), reason=verdict.refusal.value
                )
            for gesture, clip_refusal in verdict.clip_refusals.items():
                await logger.ainfo(
                    "agent_clip.refused", room_ref=room_reference(room_id), gesture=gesture, reason=clip_refusal.value
                )
            await self._update_offers(room_id)
        return outcome

    def _accept(
        self,
        person: Person,
        invitation_id: str,
        epoch: int,
        verdict: FigureVerdict,
        voices: tuple[str, ...],
        view: RoomView,
        transition: Transition,
    ) -> DialOutcome:
        live = self._live.by_person.get(person)
        room_id = view.room_id
        invitation = (
            None
            if room_id is None
            else self._places.standing_invitation(person, room_id, invitation_id, epoch, self._deadlines.now())
        )
        if live is None or room_id is None or invitation is None:
            return AgentRefusal.STALE_INVITATION
        if view.seats_in(live.owner_subject) != (invitation.sponsor,):
            return AgentRefusal.TWO_SEATS
        if view.is_parked(invitation.sponsor):
            return AgentRefusal.SPONSOR_AWAY
        agent = DialedAgent(
            secrets.token_hex(AGENT_ID_BYTES),
            live.name,
            person,
            invitation.sponsor,
            room_id,
            verdict.shown,
            voices,
        )
        if not view.has_free_place:
            return AgentRefusal.BUSY
        self._deadlines.cancel(DeadlineKind.INVITATION, invitation_key(person, room_id))
        self._seat(view, agent, transition, brought_by_sponsor=invitation.adder == invitation.sponsor)
        return agent

    async def decline(
        self, person: Person, room_id: str, invitation_id: str, epoch: int, outcome: InvitationOutcome
    ) -> AgentRefusal | None:
        """End the agent's standing invitation as its expiry does and tell the seat that made it `outcome`; the refusal,
        or nothing when it ended. A decline seats nothing, so it needs no lease."""
        refusal = await self._transitions.apply(room_id, partial(self._decline, person, invitation_id, epoch, outcome))
        if refusal is None:
            await logger.ainfo(INVITATION_CLOSED_EVENT, room_ref=room_reference(room_id), reason="declined")
        return refusal

    def _decline(
        self,
        person: Person,
        invitation_id: str,
        epoch: int,
        outcome: InvitationOutcome,
        view: RoomView,
        transition: Transition,
    ) -> AgentRefusal | None:
        invitation = (
            None
            if view.room_id is None
            else self._places.standing_invitation(person, view.room_id, invitation_id, epoch, self._deadlines.now())
        )
        if invitation is None:
            return AgentRefusal.STALE_INVITATION
        self._end_invitation(view, person, invitation, outcome, transition)
        return None

    async def agent_called(self, person: Person, own_account: OwnAccount) -> None:
        """An admitted call of an agent with an account of its own: renew its lease, or register it. A lease that ran
        out or another owner ends the old registration first."""
        admission = self._live.admit(
            person,
            own_account.owner_subject,
            own_account.name,
            now=self._deadlines.now(),
            lease_seconds=self._lease_seconds,
            new_key=partial(secrets.token_hex, AGENT_ID_BYTES),
        )
        self._live = admission.agents
        if admission.registered:
            self._arm(person, self._lease_seconds)
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
                removed_in = await self._transitions.apply_at_seat(peer_id, partial(self._on_remove, peer_id, frame))
                if removed_in is not None:
                    await self._update_offers(removed_in)
            case ParticipationSignal.AGENT_ADD:
                await self._transitions.apply_at_seat(peer_id, partial(self._on_add, peer_id, frame))
            case ParticipationSignal.AGENT_HEARING if self._hearing.enabled:
                await self._transitions.apply_at_seat(peer_id, partial(self._switches.on_frame, peer_id, frame))
            case ParticipationSignal.AGENT_SEEING:
                await self._transitions.apply_at_seat(peer_id, partial(self._switches.on_frame, peer_id, frame))
            case ParticipationSignal.AGENT_LOOK_PART:
                self._seeing.answered(peer_id, frame, part=True)
            case ParticipationSignal.AGENT_LOOK_NONE:
                self._seeing.answered(peer_id, frame, part=False)
            case _:
                return False
        return True

    async def seat_joined(self, peer_id: PeerId) -> None:
        """Show a seat that just joined the agents already in its room, and the agents it may add."""
        await self._transitions.apply_at_seat(peer_id, partial(self._seat_joined, peer_id))

    def _seat_joined(self, peer_id: PeerId, view: RoomView, transition: Transition) -> None:
        if view.room_id is None:  # pragma: no cover - a seat is in its room when it is announced
            return
        self._views[peer_id] = SeatView(view.room_id)
        for agent in [agent for agent in self._places.joined() if agent.room_id == view.room_id]:
            self._announce_joined(view, agent, [peer_id], transition)
        self._offer(view, transition)

    def chat_said(self, peer_id: PeerId, room_id: str, message: str) -> None:
        """Add a person's line to the window of every agent joined in its room, under the name the call shows."""
        self.speech.line_said(room_id, LineKind.PERSON, self._rooms.name_of(peer_id), message, sayer=None)

    def seat_changed(self, peer_id: PeerId) -> None:
        """A seat that left its room takes its places out in a transition of that room at once and holds its agents
        for `commit_seat_left`; a seat that left, parked or resumed tells the agents it sponsors."""
        bound = [
            (person, room_id, place) for person, room_id, place in self._places.entries() if place.sponsor == peer_id
        ]
        room = self._transitions.seat_view(peer_id)
        if room.room_id is None:
            seat = self._views.get(peer_id)
            if seat is not None:
                self._transitions.commit_now(seat.room_id, partial(self._take_out_places_of_departed, peer_id, bound))
        elif room.is_parked(peer_id):
            self._hearing.end_sound(peer_id)
            for _, _, place in bound:
                if isinstance(place, DialedAgent):
                    self._speaking.end(place)
                    self._seeing.ended(place)
        for person in {person for person, _, _ in bound}:
            self._on_changed(person)

    def _take_out_places_of_departed(
        self, peer_id: PeerId, bound: list[tuple[Person, str, Place]], view: RoomView, transition: Transition
    ) -> None:
        del view
        seat_view = self._views.pop(peer_id)
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
            self._agent_ended(agent, transition)
        self._hearing.seat_ended(seat_view.room_id, peer_id)
        if joined:
            self._departed[peer_id] = joined

    async def seat_left(self, peer_id: PeerId, *, room_id: str, name: str) -> None:
        """Participation hears a seat's leave in the commit that tells the room, `commit_seat_left`."""

    def commit_seat_left(self, peer_id: PeerId, *, room_id: str, name: str) -> Committed:
        """Announce the tiles of the agents the seat took with it and the agents the others may add now, then tell the
        seats that invited the owner's agents that the owner is away."""
        del name
        return self._transitions.commit_now(room_id, partial(self._seat_left, peer_id))

    def _seat_left(self, peer_id: PeerId, view: RoomView, transition: Transition) -> None:
        for agent in self._departed.pop(peer_id, []):
            self._announce_removed(agent, view.members, transition)
        self._offer(view, transition)
        for invitation in self._abandoned.pop(peer_id, []):
            self._send(
                view,
                transition,
                [invitation.adder],
                AgentInvitationEndedFrame(invitation.agent_key, InvitationOutcome.OWNER_ABSENT),
            )

    def room_ended(self, room_id: str) -> None:
        """Drop the room's places and seats; the room's seats hear the end itself."""
        self._transitions.room_ended(room_id)
        self._forget_seats_of_ended_room(room_id)
        holders = {person for person, held_room, _ in self._places.entries() if held_room == room_id}
        self._hearing.room_ended(room_id)
        ended = [agent for agent in self._places.joined() if agent.room_id == room_id]
        self._places.drop_room(room_id)
        for agent in ended:
            self._speaking.end(agent)
            self._seeing.ended(agent)
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
        await self._transitions.shut_down()
        for room_id in {agent.room_id for agent in self._places.joined()}:
            self._hearing.room_ended(room_id)
        ended = self._places.joined()
        self._places.clear()
        self.speech.stop_recovery()
        for agent in ended:
            self._speaking.end(agent)
            self._seeing.ended(agent)
        self._departed.clear()
        self._abandoned.clear()
        self._views.clear()
        self._live = LiveAgents()

    async def run_work(self, work: Work, live_seats: LiveSeats, room: WorkRoom) -> None:
        """Read the groups of the seat's session and apply what the read found to the room. The read starts before
        anything awaits after the transition that dispatched it, so its seat still sits in the room."""
        read = cast("GroupsRead", work)  # the only work participation dispatches
        groups = await self._groups_of(live_seats.connection_of(read.seat))
        await room.apply(partial(self._groups_read, read, groups))

    def stop_work(self, key: str) -> None:
        """A read waits on the issuer, so it ends only when cancelled."""
        self._transitions.cancel(key)

    def _groups_read(
        self, read: GroupsRead, groups: frozenset[str] | None, view: RoomView, transition: Transition
    ) -> None:
        """Keep the memberships a read found and act on them, unless the seat has left meanwhile: the read for an add
        decides the add, and a read for the offers asks for no other, so a failure retries at the next trigger."""
        if view.room_of(read.seat) is None:
            return
        memberships = self._view_of(read.seat, cast("str", view.room_id)).memberships
        if read.adding is None:
            for group in set(self._adders.values()):
                memberships[group] = membership_in(groups, group)
            self._offer(view, transition, read=False)
            return
        group = self._adders[read.adding.owner_subject]
        memberships[group] = membership_in(groups, group)
        refusal: AgentRefusal | None
        if memberships[group] is Membership.FAILED:
            refusal = AgentRefusal.UNAVAILABLE
        else:
            refusal = self._refusal_of_add_now(view, read.seat, read.adding, cast("PeerId", read.sponsor))
        if refusal is None:
            self._invite(view, read.seat, read.adding, cast("PeerId", read.sponsor))
        self._offer(view, transition)
        if refusal is not None:
            # After the offers: every offers frame a seat gets after a refusal is a later one, which clears it.
            transition.send(read.seat, AgentRefusedFrame(refusal).to_json())

    def _on_remove(self, peer_id: PeerId, frame: ForeignFrame, view: RoomView, transition: Transition) -> str | None:
        """Remove the agent the frame names; the room it was removed from, or nothing."""
        agent_id = frame.data.get("agent_id")
        if not isinstance(agent_id, str):
            raise InvalidMessageError("A removal needs the agent's id")
        agent = next(
            (agent for agent in self._places.joined() if agent.agent_id == agent_id and agent.room_id == view.room_id),
            None,
        )
        if agent is None:
            return None
        if peer_id != agent.sponsor and not view.is_host(peer_id):
            transition.send(peer_id, AgentRefusedFrame(AgentRefusal.NOT_SPONSOR_OR_HOST).to_json())
            return None
        self._remove(view, agent, transition)
        return agent.room_id

    def _on_add(self, peer_id: PeerId, frame: ForeignFrame, view: RoomView, transition: Transition) -> None:
        """Refuse an add that cannot stand, or read the adder's groups afresh, which then decides it."""
        agent_key = frame.data.get("agent_key")
        if not isinstance(agent_key, str):
            raise InvalidMessageError("An add needs the agent's key")
        live = None if view.room_id is None else self._live.by_key(agent_key)
        if live is None:
            transition.send(peer_id, AgentRefusedFrame(AgentRefusal.GONE).to_json())
            return
        refusal = self._refusal_of_add(view, peer_id, live, membership_is_fresh=False)
        if refusal is not None:
            transition.send(peer_id, AgentRefusedFrame(refusal).to_json())
            return
        [sponsor] = view.seats_in(live.owner_subject)  # the checks before it found exactly one
        transition.dispatch(self._group_reads.for_add(peer_id, live, sponsor))

    def _refusal_of_add_now(
        self, view: RoomView, peer_id: PeerId, live: LiveAgent, sponsor: PeerId
    ) -> AgentRefusal | None:
        """The add as it stands after the fresh read: the same registration and the same single seat of the owner."""
        current = self._live.by_person.get(live.person)
        if current is None or current.generation != live.generation:
            return AgentRefusal.GONE
        refusal = self._refusal_of_add(view, peer_id, live, membership_is_fresh=True)
        if refusal is None and view.seats_in(live.owner_subject) != (sponsor,):
            return AgentRefusal.NO_SEAT
        return refusal

    def _refusal_of_add(
        self, view: RoomView, peer_id: PeerId, live: LiveAgent, *, membership_is_fresh: bool
    ) -> AgentRefusal | None:
        """The refusals an add meets; a membership read before this add decides nothing, only the fresh one does."""
        room_id = cast("str", view.room_id)  # the adder sits in it
        sponsor = self._sole_seat(view, live.owner_subject)
        if isinstance(sponsor, AgentRefusal):
            return sponsor
        if view.is_parked(sponsor):
            return AgentRefusal.SPONSOR_AWAY
        group = self._adders.get(live.owner_subject)
        if group is None or (
            membership_is_fresh and self._view_of(peer_id, room_id).memberships.get(group) is Membership.NOT_MEMBER
        ):
            return AgentRefusal.NOT_ADDER
        if self._places.get(live.person, room_id) is not None:
            return AgentRefusal.ALREADY_THERE
        if not view.has_free_place:
            return AgentRefusal.BUSY
        return None

    def _invite(self, view: RoomView, adder: PeerId, live: LiveAgent, sponsor: PeerId) -> None:
        room_id = cast("str", view.room_id)  # the adder sits in it
        invitation = Invited(
            invitation_id=secrets.token_hex(AGENT_ID_BYTES),
            epoch=self._places.epoch_of(live.person, room_id),
            deadline=self._deadlines.now() + self._invitation_seconds,
            sponsor=sponsor,
            adder=adder,
        )
        self._places.put(live.person, room_id, invitation)
        self._deadlines.set(DeadlineKind.INVITATION, invitation_key(live.person, room_id), self._invitation_seconds)
        self._on_changed(live.person)
        logger.info("agent.invited", room_ref=room_reference(room_id))

    async def _invitation_timer(self, key: str) -> None:
        person, room_id = invitation_of(key)
        if await self._transitions.apply(room_id, partial(self._invitation_ran_out, person)):
            await logger.ainfo(INVITATION_CLOSED_EVENT, room_ref=room_reference(room_id), reason="expired")

    def _invitation_ran_out(self, person: Person, view: RoomView, transition: Transition) -> bool:
        """End the person's open invitation in the room, and say whether one was open."""
        place = None if view.room_id is None else self._places.get(person, view.room_id)
        if not isinstance(place, Invited):
            return False
        self._end_invitation(view, person, place, InvitationOutcome.NO_ANSWER, transition)
        return True

    def _end_invitation(
        self, view: RoomView, person: Person, invitation: Invited, outcome: InvitationOutcome, transition: Transition
    ) -> None:
        room_id = cast("str", view.room_id)  # the invitation is open in it
        agent_key = self._live.by_person[person].key
        self._places.end_invitation(person, room_id)
        self._deadlines.cancel(DeadlineKind.INVITATION, invitation_key(person, room_id))
        self._on_changed(person)
        self._offer(view, transition)
        # After the offers, which list the agent as available again: the outcome then marks that row.
        self._send(view, transition, [invitation.adder], AgentInvitationEndedFrame(agent_key, outcome))

    def _arm(self, person: Person, seconds: float) -> None:
        self._deadlines.set(DeadlineKind.LEASE, person.key, seconds)

    async def _lease_timer(self, key: str) -> None:
        person = Person.from_key(key)
        current = self._live.by_person.get(person)
        if current is None:
            return
        check = self._live.checked(person, now=self._deadlines.now())
        self._live = check.agents
        if check.ended is None:
            self._arm(person, check.rearm_after)
        else:
            await self._end_lease(check.ended)

    async def _end_lease(self, ended: LiveAgent) -> None:
        """End every place of the agent, committing every room's transition before any seat hears of it; then signal
        the standing and tell the rooms what they may add."""
        rooms = [room_id for room_id, _ in self._places.of_person(ended.person)]
        await self._transitions.apply_each(rooms, partial(self._lease_ended_in, ended.person))
        self._on_changed(ended.person)
        await self._update_every_room_offers()

    def _lease_ended_in(self, person: Person, view: RoomView, transition: Transition) -> None:
        room_id = cast("str", view.room_id)  # the person's place is in it, and the room's end drops its places
        place = self._places.get(person, room_id)
        self._places.remove(person, room_id)
        if isinstance(place, DialedAgent):
            self._agent_ended(place, transition)
            self._announce_removed(place, view.members, transition)

    def _standing_in(self, room_id: str, place: Invited | DialedAgent) -> PlaceStanding:
        if isinstance(place, Invited):
            expires_at = self._wall_clock() + place.deadline - self._deadlines.now()
            return PlaceStanding(
                room_id, PlaceState.INVITED, InvitationStanding(place.invitation_id, place.epoch, expires_at)
            )
        state = PlaceState.PAUSED if self._transitions.view(room_id).is_parked(place.sponsor) else PlaceState.ACTIVE
        return PlaceStanding(room_id, state)

    async def _write_line(self, agent: DialedAgent, line: str) -> None:
        """Show the agent's written line to every joined agent's window and every seat in its room."""
        self.speech.line_said(agent.room_id, LineKind.AGENT, agent.name, line, sayer=agent.person)
        await self._tell(
            agent.room_id, self._recipients(agent.room_id), AgentLineFrame(agent.agent_id, agent.name, line)
        )

    def _active_agent(self, person: Person, room_id: str) -> DialedAgent | AgentRefusal:
        """The person's agent in the room when it is joined and its sponsor's seat is connected, else why it is not."""
        agent = self._places.get(person, room_id)
        if not isinstance(agent, DialedAgent):
            return AgentRefusal.GONE
        return AgentRefusal.SPONSOR_AWAY if self._rooms.is_parked(agent.sponsor) else agent

    def _no_room_to_speak(self, agent: DialedAgent) -> SpeakFailure | None:
        """Why the agent may not queue one more sentence, or nothing when it may."""
        if self._speaking.is_full(agent):
            return SpeakFailure.QUEUE_FULL
        return None

    def _unavailable(self, agent: DialedAgent, text: str, failure: SpeakFailure) -> AgentRefusal:
        """Answer `unavailable` for a sentence never rendered, its closed cause on its speak line and nothing else."""
        with self.speech.speak_step(agent, text) as step:
            step.refuse(failure.value)
        return AgentRefusal.UNAVAILABLE

    def _agent_ended(self, agent: DialedAgent, transition: Transition) -> None:
        """The agent's removal, leave or lease end: it frees its place and hears and speaks nothing more."""
        transition.release_agent_place(agent.agent_id)
        self._hearing.agent_ended(agent)
        self._speaking.end(agent)
        self._seeing.ended(agent)

    async def _sentence_started(self, agent: DialedAgent, sentence: QueuedSentence) -> None:
        """A sentence of the agent begins to play: it enters the call's chat as the agent's line, and every seat is told
        its voice."""
        self.speech.sentence_started(agent)
        recipients = self._recipients(agent.room_id)
        await self._tell(agent.room_id, recipients, AgentLineFrame(agent.agent_id, agent.name, sentence.text))
        for peer_id in recipients:
            if not self._speaking.is_playing(agent, sentence):
                return
            await self._tell(agent.room_id, [peer_id], AgentVoiceFrame(agent.agent_id, sentence.voice.id))

    @staticmethod
    def _sole_seat(view: RoomView, owner_subject: str) -> PeerId | AgentRefusal:
        seats = view.seats_in(owner_subject)
        if not seats:
            return AgentRefusal.NO_SEAT
        if len(seats) > 1:
            return AgentRefusal.TWO_SEATS
        return seats[0]

    def _view_of(self, peer_id: PeerId, room_id: str) -> SeatView:
        return self._views.setdefault(peer_id, SeatView(room_id))

    def _recipients(self, room_id: str) -> list[PeerId]:
        """The room's seats now, none once the room has ended."""
        return list(self._transitions.view(room_id).members)

    def _remove(self, view: RoomView, agent: DialedAgent, transition: Transition) -> None:
        self._places.remove(agent.person, agent.room_id)
        self._agent_ended(agent, transition)
        self._on_changed(agent.person)
        self._announce_removed(agent, view.members, transition)

    def _announce_removed(self, agent: DialedAgent, recipients: Sequence[PeerId], transition: Transition) -> None:
        # A tile not yet written to a seat never shows there once its agent has left.
        unannounced = Supersedes(frozenset({ParticipationSignal.AGENT_JOINED}), holding=("agent_id", agent.agent_id))
        for peer_id in recipients:
            transition.send(peer_id, AgentLeftFrame(agent.agent_id).to_json(), unannounced)
        logger.info("agent.left", room_ref=room_reference(agent.room_id), agent_id=agent.agent_id)

    def _announce_joined(
        self, view: RoomView, agent: DialedAgent, peer_ids: Sequence[PeerId], transition: Transition
    ) -> None:
        """Tell the seats of the agent's tile."""
        frame = joined_frame(agent, view.name_of(agent.sponsor))
        for peer_id in peer_ids:
            transition.send(peer_id, frame.to_json())

    @staticmethod
    def _send(view: RoomView, transition: Transition, peer_ids: Sequence[PeerId], frame: ParticipationFrame) -> None:
        """Send to each seat that still sits in the room."""
        for peer_id in peer_ids:
            if view.room_of(peer_id) is not None:
                transition.send(peer_id, frame.to_json())

    async def _tell(self, room_id: str, peer_ids: Sequence[str], frame: ParticipationFrame) -> None:
        """Send to each seat that still sits in the room: a room that has ended gets nothing more."""
        seats = [PeerId(peer_id) for peer_id in peer_ids]
        await self._transitions.apply(room_id, partial(self._send, peer_ids=seats, frame=frame))

    async def _update_every_room_offers(self) -> None:
        for room_id in sorted({view.room_id for view in self._views.values()}):
            await self._update_offers(room_id)

    async def _update_offers(self, room_id: str, *, read: bool = True) -> None:
        await self._transitions.apply(room_id, partial(self._offer, read=read))

    def _offer(self, view: RoomView, transition: Transition, *, read: bool = True) -> None:
        """Send each seat the agents it may add, whether its control shows and whether no place is free for an agent,
        when they differ from what it was last sent. `read` starts the reads of memberships still unknown or failed;
        the read that just ended asks for none, so a failure retries at the next trigger."""
        if view.room_id is None:  # the room has ended
            return
        if read:
            self._read_groups(view, transition)
        full = not view.has_free_place
        for peer_id in view.members:
            seat = self._view_of(peer_id, view.room_id)
            offers = self._offers_for(view, peer_id)
            shown = self._control_shown_to(view, peer_id, offers)
            if (offers, shown, full) != (seat.offered, seat.shown, seat.full):
                seat.offered = offers
                seat.shown = shown
                seat.full = full
                transition.send(peer_id, AgentOffersFrame(shown=shown, full=full, offers=list(offers)).to_json())

    def _read_groups(self, view: RoomView, transition: Transition) -> None:
        """Read the groups of each seat whose membership in an adders group is unknown or failed to read, unless a
        read of them runs."""
        groups = set(self._adders.values())
        for peer_id in view.members:
            memberships = self._view_of(peer_id, cast("str", view.room_id)).memberships
            read = self._group_reads.for_offers(peer_id)
            if (
                any(memberships.get(group, Membership.FAILED) is Membership.FAILED for group in groups)
                and not self._transitions.is_working(read.key)
                and read not in transition.dispatched
            ):
                transition.dispatch(read)

    def _control_shown_to(self, view: RoomView, peer_id: PeerId, offers: Sequence[Offer]) -> bool:
        """The control shows to a seat with an offer, to the call's host and to a member of an adders group, even with
        no agent to list; it never waits on a fresh read."""
        if offers:
            return True
        memberships = self._view_of(peer_id, cast("str", view.room_id)).memberships
        return view.is_host(peer_id) or any(
            memberships.get(group) is Membership.MEMBER for group in self._adders.values()
        )

    def _offers_for(self, view: RoomView, peer_id: PeerId) -> tuple[Offer, ...]:
        """The agents a seat may add: each with a lease that runs and a listen on its standing that is open, or ended
        within its allowance."""
        room_id = cast("str", view.room_id)
        memberships = self._view_of(peer_id, room_id).memberships
        offers = []
        for live in self._live.by_person.values():
            group = self._adders.get(live.owner_subject)
            sponsor = self._sole_seat(view, live.owner_subject)
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
            elif place is None and view.has_free_place:
                offers.append(Offer(live.key, live.name, pending=False))
        return tuple(offers)
