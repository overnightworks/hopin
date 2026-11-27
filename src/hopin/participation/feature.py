import secrets
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Final, cast

import structlog
from agents.calling import AcceptRefused, Ended, OpenInvitation, after_send_away, check_accept, check_decline, expired
from agents.member import DeclineInvitationRefusal

from hopin.errors import InvalidMessageError
from hopin.participation.agent_speech import AgentSpeech
from hopin.participation.chat_window import LineKind, WindowRead
from hopin.participation.figures import Figure, FigureSource, judge_figure
from hopin.participation.frames import (
    AgentInvitationEndedFrame,
    AgentLineFrame,
    AgentRefusedFrame,
    ParticipationFrame,
)
from hopin.participation.group_reads import Adding, SendingAway
from hopin.participation.line_budget import full_budget, spent_on_a_line
from hopin.participation.live_agents import LiveAgents
from hopin.participation.memberships import Membership, SeatViews
from hopin.participation.offers import Offers
from hopin.participation.participants import DialedAgent, DialOutcome, OwnAccount, Person
from hopin.participation.places import (
    AbandonedInvitation,
    InvitationStanding,
    Places,
    PlaceStanding,
    PlaceState,
    invitation_key,
    invitation_of,
)
from hopin.participation.seeing import Seeing
from hopin.participation.shown_figures import ShownFigures
from hopin.participation.speech_in_windows import SpeechInWindows
from hopin.participation.switches import Switches
from hopin.participation.telling import send_in_room, tell_joined, tell_left
from hopin.participation.text import clean_line, clean_name
from hopin.participation.video_looks import VideoLooks
from hopin.participation.vocabulary import (
    AGENT_ID_BYTES,
    FEATURE_NAME,
    FIGURE_MODULE,
    SIGNAL_TYPES,
    AgentRefusal,
    FigureMood,
    HearingRefusal,
    HoldRefusal,
    LookRefusal,
    ParticipationSignal,
)
from hopin.rooms.reference import room_reference
from hopin.signaling.features import TransitionFeature
from hopin.signaling.frames import PeerId
from hopin.signaling.scheduler import DeadlineKind

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping, Sequence

    from agents.member import AcceptInvitationArguments, DeclineInvitationArguments

    from hopin.participation.figures import FigureVerdict
    from hopin.participation.group_reads import GroupsOf, GroupsRead
    from hopin.participation.hearing import Hearing, HearingHandle
    from hopin.participation.live_agents import LiveAgent
    from hopin.participation.looking import Picture
    from hopin.participation.places import Place
    from hopin.participation.speaking import Renderer, SpeakResult, StopResult, VoicesOffered
    from hopin.participation.voice import VoiceStore
    from hopin.signaling.frames import ForeignFrame
    from hopin.signaling.replay import Supersedes
    from hopin.signaling.scheduler import Deadlines
    from hopin.signaling.transition import (
        Change,
        FeatureTransitions,
        LiveSeats,
        RoomView,
        Transition,
        Work,
        WorkRoom,
    )

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

INVITATION_CLOSED_EVENT: Final = "agent_invitation.closed"
"""The log event of an invitation that ends, with the reason it ended."""


class Participation(TransitionFeature):
    """The agents in calls: dialed in by a person's own agent or added by a member of the realm group of agent adders,
    whichever seat sponsors them; each takes a place of the call like a person."""

    def __init__(
        self,
        deadlines: Deadlines,
        lease_seconds: float,
        on_changed: Callable[[Person], None],
        on_window_changed: Callable[[Person, str], None],
        wall_clock: Callable[[], float],
        groups_of: GroupsOf,
        listens_to_standing: Callable[[Person], bool],
        figure_sources: Collection[FigureSource],
        hearing: Hearing,
        voices: VoiceStore,
    ) -> None:
        self._transitions: FeatureTransitions
        self._deadlines = deadlines
        deadlines.register(DeadlineKind.INVITATION, self._invitation_timer)
        deadlines.register(DeadlineKind.LEASE, self._lease_timer)
        self._lease_seconds = lease_seconds
        self._on_changed = on_changed
        self._on_window_changed = on_window_changed
        self._wall_clock = wall_clock
        self._groups_of = groups_of
        self._figure_sources = figure_sources
        self._hearing = hearing
        self._places = Places()
        self._seat_views = SeatViews()
        self._offers = Offers(self._places, listens_to_standing, self._is_working, self._seat_views)
        self.speech = SpeechInWindows(self._places, hearing, self._name_of, on_window_changed, deadlines)
        self.figures = ShownFigures(self._places, deadlines, self._active_agent, self._tell, self._recipients)
        self._agent_speech = AgentSpeech(
            voices, deadlines, self.speech, self.figures, self._write_line, self._tell_room
        )
        self._seeing = Seeing(hearing, self._places, deadlines, self._tell, self._recipients)
        self.video_looks = VideoLooks(self._places)
        self._switches = Switches(self._places, hearing, self.speech, self._seeing)
        self._live = LiveAgents()
        self._departed: dict[PeerId, list[DialedAgent]] = {}
        self._abandoned: dict[PeerId, list[AbandonedInvitation]] = {}

    @property
    def name(self) -> str:
        return FEATURE_NAME

    def attach(self, transitions: FeatureTransitions) -> None:
        self._transitions = transitions
        self._agent_speech.attach(transitions)

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
        tell_joined(view, agent, self.figures.framing_of(agent), view.members, transition)
        self._switches.tell(agent, transition)

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
        self._places.take_out(agent.person, agent.room_id)
        self._removed(view, agent, transition)
        return None

    async def say(self, person: Person, room_id: str, text: str) -> AgentRefusal | None:
        """Show the person's agent's line to every seat in the room; the refusal, or nothing when it was sent."""
        agent = self._places.get(person, room_id)
        if not isinstance(agent, DialedAgent):
            return AgentRefusal.GONE
        line = clean_line(text)
        if line is None:
            return AgentRefusal.BAD_LINE
        await self.figures.end_turn_state(agent)
        await self._write_line(agent, line)
        return None

    async def speak(
        self,
        person: Person,
        room_id: str,
        text: str,
        render: Renderer | None,
        offered: VoicesOffered | None = None,
        mood: FigureMood | None = None,
        interrupted: str | None = None,
    ) -> SpeakResult:
        """Queue the person's agent's sentence (`AgentSpeech.speak`); the refusal when the agent holds no active place
        there."""
        agent = self._active_agent(person, room_id)
        if isinstance(agent, AgentRefusal):
            return agent
        return await self._agent_speech.speak(agent, text, render, offered, mood, interrupted)

    async def stop_speaking(self, person: Person, room_id: str, interrupted: str | None = None) -> StopResult:
        """Cut the sentence the person's agent plays or holds and drop its others (`AgentSpeech.stop`); the refusal when
        the agent holds no active place there."""
        agent = self._active_agent(person, room_id)
        if isinstance(agent, AgentRefusal):
            return agent
        return await self._agent_speech.stop(agent, interrupted)

    async def continue_speaking(
        self, person: Person, room_id: str, interrupted: str
    ) -> AgentRefusal | HoldRefusal | None:
        """End the hold of the person's agent that `interrupted` names (`AgentSpeech.continue_speaking`); the refusal,
        or nothing when its held sentence goes on."""
        agent = self._active_agent(person, room_id)
        if isinstance(agent, AgentRefusal):
            return agent
        return await self._agent_speech.continue_speaking(agent, interrupted)

    async def person_started(self, room_id: str, seat: PeerId) -> None:
        """A person starts speaking: the windows say so, then each agent that hears the seat and speaks is held."""
        await self._agent_speech.person_started(room_id, seat)

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
        if window is None:
            return None
        agent = self._places.get(person, room_id)
        assert isinstance(agent, DialedAgent)  # noqa: S101 - only a joined agent holds a window
        return window.read(interrupted=self._agent_speech.hold_of(agent), playing=self._agent_speech.plays(agent))

    def standings_of(self, person: Person) -> tuple[PlaceStanding, ...]:
        """Every place the person holds as it reads now; a place it was removed from is absent."""
        return tuple(self._standing_in(person, room_id, place) for room_id, place in self._places.of_person(person))

    def open_hearing(
        self, account: Person, seat: PeerId, holds_token: Callable[[Person], bool]
    ) -> HearingHandle | HearingRefusal:
        """The sound session of a seat the account holds in a room, served by an agent that hears it and ending with
        that agent's hearing; refused for a seat the account lacks, one waiting to resume or one nobody hears."""
        view = self._transitions.seat_view(seat)
        if view.room_id is None or seat not in view.seats_in(account.subject):
            return HearingRefusal.NO_SEAT
        if view.is_parked(seat):
            return HearingRefusal.SEAT_AWAY
        return self._hearing.open(account, view.room_id, seat, holds_token)

    def heard(self, seat: PeerId, text: str, began_at: float, item_id: str) -> None:
        """Add a finished sentence the seat said, which began at `began_at` on the deadlines' clock and which the
        speech service knows as `item_id`, to the window of each joined agent that hears the seat, under the name the
        call shows for the seat."""
        room_id = self._transitions.seat_view(seat).room_id
        if room_id is None:
            return
        self.speech.person_said(room_id, seat, text, began_at, item_id)

    def is_live_agent(self, person: Person) -> bool:
        """Whether the person is an agent with an account of its own whose lease is registered."""
        return person in self._live.by_person

    async def accept(
        self,
        person: Person,
        accepted: AcceptInvitationArguments,
        figure: Figure | None,
        voices: tuple[str, ...] = (),
    ) -> DialOutcome:
        """Turn the agent's open invitation into its tile, bound to the owner's seat the invitation names; the tile
        shows the figure only when an allowed source holds it, and the agent keeps `voices` for its sentences."""
        room_id = accepted.place.id
        room_ref = room_reference(room_id)
        verdict = judge_figure(self._figure_sources, figure)
        outcome = await self._transitions.apply(room_id, partial(self._accept, person, accepted, verdict, voices))
        if isinstance(outcome, DialedAgent):
            await logger.ainfo(INVITATION_CLOSED_EVENT, room_ref=room_ref, reason="accepted")
            await self.figures.log_refusals(room_id, verdict)
            await self._update_offers(room_id)
        elif outcome is not AgentRefusal.STALE_INVITATION:
            await logger.ainfo(INVITATION_CLOSED_EVENT, room_ref=room_ref, reason="refused")
        return outcome

    def _accept(
        self,
        person: Person,
        accepted: AcceptInvitationArguments,
        verdict: FigureVerdict,
        voices: tuple[str, ...],
        view: RoomView,
        transition: Transition,
    ) -> DialOutcome:
        live = self._live.by_person.get(person)
        room_id = view.room_id
        entry = None if room_id is None else self._places.entry(person, room_id)
        invitation = None if entry is None else entry.invitation
        if live is None or room_id is None or entry is None or invitation is None:
            return AgentRefusal.STALE_INVITATION
        facts = self._offers.accept_facts(view, live, invitation)
        decided = check_accept(entry, accepted, facts, now=self._now())
        if isinstance(decided, AcceptRefused):
            if decided.ended is not None:
                self._end_invitation(view, person, decided.ended, transition)
            return AgentRefusal(decided.refusal.value)
        sponsor = PeerId(invitation.bound_to)
        agent_id = secrets.token_hex(AGENT_ID_BYTES)
        agent = DialedAgent(agent_id, live.name, person, sponsor, room_id, verdict.shown, voices, invitation.owner)
        self._places.write(person, room_id, decided.entry)
        self._deadlines.cancel(DeadlineKind.INVITATION, invitation_key(person, room_id))
        self._seat(view, agent, transition, brought_by_sponsor=invitation.inviter == invitation.bound_to)
        return agent

    async def decline(self, person: Person, declined: DeclineInvitationArguments) -> DeclineInvitationRefusal | None:
        """End the agent's standing invitation as its expiry does and tell the seat that made it the outcome of the
        reason; the refusal, or nothing when it ended. A decline seats nothing, so it needs no lease."""
        room_id = declined.place.id
        refusal = await self._transitions.apply(room_id, partial(self._decline, person, declined))
        if refusal is None:
            await logger.ainfo(INVITATION_CLOSED_EVENT, room_ref=room_reference(room_id), reason="declined")
        return refusal

    def _decline(
        self, person: Person, declined: DeclineInvitationArguments, view: RoomView, transition: Transition
    ) -> DeclineInvitationRefusal | None:
        if view.room_id is None:
            return DeclineInvitationRefusal.STALE_INVITATION
        decided = check_decline(self._places.entry(person, view.room_id), declined, now=self._now())
        if isinstance(decided, DeclineInvitationRefusal):
            return decided
        self._end_invitation(view, person, decided, transition)
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

    def on_frame(self, view: RoomView, peer_id: PeerId, frame: ForeignFrame, transition: Transition) -> bool:
        match frame.signal_type:
            case ParticipationSignal.AGENT_REMOVE:
                if agent := self._offers.on_send_away(peer_id, frame, view, transition):
                    self._send_away(view, agent, transition)
            case ParticipationSignal.AGENT_ADD:
                self._offers.on_add(self._live, peer_id, frame, view, transition)
            case ParticipationSignal.LINE_FOR_AGENTS:
                self._on_line_for_agents(peer_id, frame, view)
            case ParticipationSignal.AGENT_HEARING if self._hearing.enabled:
                self._switches.on_frame(peer_id, frame, view, transition)
            case ParticipationSignal.AGENT_SEEING:
                self._switches.on_frame(peer_id, frame, view, transition)
            case ParticipationSignal.AGENT_FIGURE_ENDED:
                self.figures.ended(peer_id, frame, view)
            case ParticipationSignal.AGENT_LOOK_PART:
                self._seeing.answered(peer_id, frame, part=True)
            case ParticipationSignal.AGENT_LOOK_NONE:
                self._seeing.answered(peer_id, frame, part=False)
            case _:
                return False
        return True

    def seat_joined(self, view: RoomView, peer_id: PeerId, transition: Transition) -> None:
        """Show a seat that just joined the agents already in its room, and the agents it may add."""
        if view.room_id is None:  # pragma: no cover - a seat is in its room when it is announced
            return
        self._seat_views.joined(peer_id, view.room_id)
        for agent in self._places.joined_in(view.room_id):
            tell_joined(view, agent, self.figures.framing_of(agent), [peer_id], transition)
            self.figures.tell_turn_state(agent, peer_id, transition)
            self._agent_speech.tell_held(view, transition, agent, peer_id)
        self._offer(view, transition)

    def _on_line_for_agents(self, peer_id: PeerId, frame: ForeignFrame, view: RoomView) -> None:
        """Add a person's line to the window of every agent joined in the sender's room, under the name the call shows;
        a line beyond the limits, or one nobody reads, is dropped."""
        text = frame.data.get("text")
        if not isinstance(text, str):
            raise InvalidMessageError("A line for agents needs its text")
        line = clean_line(text)
        if line is None or view.room_id is None:
            return
        seat = self._seat_views.of(peer_id, view.room_id)
        now = self._deadlines.now()
        budget = spent_on_a_line(seat.line_budget or full_budget(now), now)
        if budget is None:
            return
        seat.line_budget = budget
        self.speech.line_said(view.room_id, LineKind.PERSON, view.name_of(peer_id), line, sayer=None)

    def seat_changed(self, view: RoomView, peer_id: PeerId, change: Change) -> None:
        """A seat that left its room takes its places out in a change of that room at once and holds its agents for
        `seat_left`; a seat that left, parked or resumed tells the agents it sponsors."""
        del change
        bound = self._places.bound_to(peer_id)
        if view.room_id is None:
            seat_room = self._seat_views.room_of(peer_id)
            if seat_room is not None:
                self._transitions.change_now(seat_room, partial(self._take_out_places_of_departed, peer_id, bound))
        elif view.is_parked(peer_id):
            self._hearing.end_sound(peer_id)
            for _, _, place in bound:
                if isinstance(place, DialedAgent):
                    self._agent_speech.park(place)
                    self._seeing.ended(place)
        for person in {person for person, _, _ in bound}:
            self._on_changed(person)

    def _take_out_places_of_departed(
        self, peer_id: PeerId, bound: list[tuple[Person, str, Place]], view: RoomView, change: Change
    ) -> None:
        del view
        seat_room = self._seat_views.left(peer_id)
        now = self._now()
        invitations = []
        for person, room_id, place in bound:
            if isinstance(place, OpenInvitation):
                ended = self._places.end_for_departed_owner(person, room_id, now)
                called = self._live.by_person[person]
                invitations.append(
                    AbandonedInvitation(room_id, called.key, called.name, PeerId(ended.inviter), ended.outcome)
                )
                self._deadlines.cancel(DeadlineKind.INVITATION, invitation_key(person, room_id))
            else:
                self._places.take_out(person, room_id)
        if invitations:
            self._abandoned[peer_id] = invitations
        joined = [place for _, _, place in bound if isinstance(place, DialedAgent)]
        for agent in joined:
            self._agent_ended(agent, change)
        self._hearing.seat_ended(seat_room, peer_id)
        if joined:
            self._departed[peer_id] = joined

    def seat_left(self, view: RoomView, peer_id: PeerId, transition: Transition) -> None:
        """Announce the tiles of the agents the seat took with it and the agents the others may add now, then tell the
        seats that invited the owner's agents how their invitation ended."""
        self.figures.seat_left(peer_id)
        for agent in self._departed.pop(peer_id, []):
            tell_left(agent, view.members, transition)
        self._offer(view, transition)
        for invitation in self._abandoned.pop(peer_id, []):
            send_in_room(
                view,
                transition,
                [invitation.inviter],
                AgentInvitationEndedFrame(invitation.agent_key, invitation.name, invitation.outcome),
            )

    def room_ended(self, room_id: str) -> None:
        """Drop the room's places and seats; the room's seats hear the end itself."""
        self._forget_seats_of_ended_room(room_id)
        holders = {person for person, held_room, _ in self._places.entries() if held_room == room_id}
        self._hearing.room_ended(room_id)
        ended = [agent for agent in self._places.joined() if agent.room_id == room_id]
        self._places.drop_room(room_id)
        for agent in ended:
            self._end_looks_and_voice(agent)
        for person in holders:
            self._on_changed(person)

    def _forget_seats_of_ended_room(self, room_id: str) -> None:
        for sponsor in [sponsor for sponsor, gone in self._departed.items() if gone[0].room_id == room_id]:
            del self._departed[sponsor]
        for sponsor in [sponsor for sponsor, gone in self._abandoned.items() if gone[0].room_id == room_id]:
            del self._abandoned[sponsor]
        self._seat_views.room_ended(room_id)

    async def shut_down(self) -> None:  # NOSONAR(S7503) the call core awaits a feature's shutdown
        for room_id in {agent.room_id for agent in self._places.joined()}:
            self._hearing.room_ended(room_id)
        ended = self._places.joined()
        self._places.clear()
        self.speech.stop_recovery()
        for agent in ended:
            self._end_looks_and_voice(agent)
        self._departed.clear()
        self._abandoned.clear()
        self._seat_views.clear()
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
        """Keep the memberships a read found and let the act it was for decide on them, unless the seat has left
        meanwhile; a read for the offers asks for no act, so a failure retries at the next trigger."""
        if view.room_of(read.seat) is None:
            return
        membership = self._offers.keep(read, groups, cast("str", view.room_id))
        match read.act:
            case Adding() as adding:
                self._added(view, read.seat, adding, membership, transition)
            case SendingAway() as sending:
                self._sent_away(view, read.seat, sending, membership, transition)
            case _:
                self._offer(view, transition, read=False)

    def _added(
        self, view: RoomView, seat: PeerId, adding: Adding, membership: Membership, transition: Transition
    ) -> None:
        refusal: AgentRefusal | None
        lapsed: Ended | None = None
        if membership is Membership.FAILED:
            refusal = AgentRefusal.UNAVAILABLE
        else:
            now = self._now()
            lapsed = self._close_lapsed(view, adding.agent.person, now)
            refusal = self._invite(view, seat, adding.agent, now, inviter_is_member=membership is Membership.MEMBER)
        self._offer(view, transition)
        if lapsed is not None:
            # The call replaced an invitation that ran out unnoticed; its inviter hears of it after the final offers.
            self._tell_outcome(view, adding.agent.person, lapsed, transition)
        if refusal is not None:
            # After the offers: every offers frame a seat gets after a refusal is a later one, which clears it.
            transition.send(seat, AgentRefusedFrame(refusal).to_json())

    def _sent_away(
        self, view: RoomView, seat: PeerId, sending: SendingAway, membership: Membership, transition: Transition
    ) -> None:
        """Send the agent away when the call as the read leaves it allows it, else the offers go before any refusal."""
        decided = self._offers.after_send_away_read(view, seat, sending.agent, membership)
        if isinstance(decided, DialedAgent):
            self._send_away(view, decided, transition)
            return
        self._offer(view, transition)
        if decided is not None:
            transition.send(seat, AgentRefusedFrame(decided).to_json())

    def _close_lapsed(self, view: RoomView, person: Person, now: datetime) -> Ended | None:
        """End the person's invitation in the room when the wall clock lapsed it, so the call replaces it with a new
        one; the end its inviter has yet to hear of, or nothing."""
        room_id = cast("str", view.room_id)  # the inviter sits in it
        lapsed = expired(self._places.entry(person, room_id), now=now)
        if lapsed is not None:
            self._close_invitation(person, room_id, lapsed)
            logger.info(INVITATION_CLOSED_EVENT, room_ref=room_reference(room_id), reason="expired")
        return lapsed

    def _invite(
        self, view: RoomView, inviter: PeerId, live: LiveAgent, now: datetime, *, inviter_is_member: bool
    ) -> AgentRefusal | None:
        """Call the agent as the calling standard decides on the facts of the call now: the refusal, or nothing when an
        invitation stands. A repeated call by the seat that made the open invitation changes nothing."""
        room_id = cast("str", view.room_id)  # the inviter sits in it
        before = self._places.entry(live.person, room_id)
        decided = self._offers.call(
            view,
            inviter,
            live,
            self._live,
            inviter_is_member=inviter_is_member,
            invitation_id=secrets.token_hex(AGENT_ID_BYTES),
            now=now,
        )
        if isinstance(decided, AgentRefusal):
            return decided
        if decided.entry != before:
            self._places.write(live.person, room_id, decided.entry)
            self._arm_invitation(live.person, room_id, decided.invitation.expires_at, now)
            self._on_changed(live.person)
            logger.info("agent.invited", room_ref=room_reference(room_id))
        return None

    def _arm_invitation(self, person: Person, room_id: str, expires_at: datetime, now: datetime) -> None:
        """Arm the invitation's timer to fire when it expires; the wall clock decides whether it has."""
        self._deadlines.set(
            DeadlineKind.INVITATION, invitation_key(person, room_id), (expires_at - now).total_seconds()
        )

    async def _invitation_timer(self, key: str) -> None:
        person, room_id = invitation_of(key)
        if await self._transitions.apply(room_id, partial(self._invitation_ran_out, person)):
            await logger.ainfo(INVITATION_CLOSED_EVENT, room_ref=room_reference(room_id), reason="expired")

    def _invitation_ran_out(self, person: Person, view: RoomView, transition: Transition) -> bool:
        """End the person's invitation in the room when it ran out, and say whether one ended; an invitation the wall
        clock still holds open arms the timer for the rest."""
        if view.room_id is None:
            return False
        entry = self._places.entry(person, view.room_id)
        now = self._now()
        ended = expired(entry, now=now)
        if ended is not None:
            self._end_invitation(view, person, ended, transition)
            return True
        if entry.invitation is not None:
            self._arm_invitation(person, view.room_id, entry.invitation.expires_at, now)
        return False

    def _end_invitation(self, view: RoomView, person: Person, ended: Ended, transition: Transition) -> None:
        self._close_invitation(person, cast("str", view.room_id), ended)  # the invitation is open in it
        self._offer(view, transition)
        self._tell_outcome(view, person, ended, transition)

    def _close_invitation(self, person: Person, room_id: str, ended: Ended) -> None:
        self._places.write(person, room_id, ended.entry)
        self._deadlines.cancel(DeadlineKind.INVITATION, invitation_key(person, room_id))
        self._on_changed(person)

    def _tell_outcome(self, view: RoomView, person: Person, ended: Ended, transition: Transition) -> None:
        """Tell the inviter the outcome; callers send it after the final offers, so no later offers frame clears it."""
        agent = self._live.by_person[person]
        send_in_room(
            view, transition, [PeerId(ended.inviter)], AgentInvitationEndedFrame(agent.key, agent.name, ended.outcome)
        )

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
        self._places.take_out(person, room_id)
        if isinstance(place, DialedAgent):
            self._agent_ended(place, transition)
            tell_left(place, view.members, transition)

    def _standing_in(self, person: Person, room_id: str, place: Place) -> PlaceStanding:
        if isinstance(place, OpenInvitation):
            invitation = InvitationStanding(place.id, self._places.epoch_of(person, room_id), place.expires_at)
            return PlaceStanding(room_id, PlaceState.INVITED, invitation)
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
        return AgentRefusal.SPONSOR_AWAY if self._transitions.view(room_id).is_parked(agent.sponsor) else agent

    def _agent_ended(self, agent: DialedAgent, change: Change) -> None:
        """The agent's removal, leave or lease end: it frees its place and hears and speaks nothing more."""
        change.release_agent_place(agent.agent_id)
        self._hearing.agent_ended(agent)
        self._end_looks_and_voice(agent)

    def _end_looks_and_voice(self, agent: DialedAgent) -> None:
        self._agent_speech.end(agent)
        self._seeing.ended(agent)
        self.figures.agent_ended(agent)
        self.video_looks.agent_ended(agent)

    def _now(self) -> datetime:
        """The wall clock's time, which every decision of the calling standard reads."""
        return datetime.fromtimestamp(self._wall_clock(), UTC)

    def _is_working(self, key: str) -> bool:
        return self._transitions.is_working(key)

    def _name_of(self, seat: PeerId) -> str:
        """The name the call shows for a seat that sits in a room."""
        return self._transitions.seat_view(seat).name_of(seat)

    def _recipients(self, room_id: str) -> list[PeerId]:
        """The room's seats now, none once the room has ended."""
        return list(self._transitions.view(room_id).members)

    def _send_away(self, view: RoomView, agent: DialedAgent, transition: Transition) -> None:
        """Take the agent away as the calling standard's send-away decides, and tell every seat, offers included."""
        row = after_send_away(self._places.entry(agent.person, agent.room_id))
        self._places.remove(agent.person, agent.room_id, row)
        self._removed(view, agent, transition)
        transition.follow(self._offer)

    def _removed(self, view: RoomView, agent: DialedAgent, transition: Transition) -> None:
        self._agent_ended(agent, transition)
        self._on_changed(agent.person)
        tell_left(agent, view.members, transition)

    async def _tell_room(self, agent: DialedAgent, frame: ParticipationFrame, supersedes: Supersedes | None) -> None:
        """Send the frame to every seat in the agent's room."""
        await self._tell(agent.room_id, self._recipients(agent.room_id), frame, supersedes)

    async def _tell(
        self, room_id: str, peer_ids: Sequence[str], frame: ParticipationFrame, supersedes: Supersedes | None = None
    ) -> None:
        """Send to each seat that still sits in the room: a room that has ended gets nothing more."""
        seats = [PeerId(peer_id) for peer_id in peer_ids]
        await self._transitions.apply(
            room_id, partial(send_in_room, peer_ids=seats, frame=frame, supersedes=supersedes)
        )

    async def _update_every_room_offers(self) -> None:
        for room_id in self._seat_views.rooms():
            await self._update_offers(room_id)

    async def _update_offers(self, room_id: str, *, read: bool = True) -> None:
        await self._transitions.apply(room_id, partial(self._offer, read=read))

    def _offer(self, view: RoomView, transition: Transition, *, read: bool = True) -> None:
        self._offers.send(view, transition, self._live, read=read)
