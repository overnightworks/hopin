"""What each seat of a call may add and send away: the agents it is offered, whether its control shows, the joined
agents it may send away, and the facts the calling standard decides an add and a send-away on, as the seats' membership
in the realm group of agent adders a read of their groups found it and the call's seats give them."""

from typing import TYPE_CHECKING, cast

from agents.calling import AcceptFacts, CallFacts, Invited, OpenInvitation, OwnerAt, invite, send_away_allowed

from hopin.errors import InvalidMessageError
from hopin.participation.frames import AgentOffersFrame, AgentRefusedFrame, Offer
from hopin.participation.group_reads import GroupReads
from hopin.participation.memberships import AGENT_ADDERS_GROUP, Membership, membership_in
from hopin.participation.participants import DialedAgent
from hopin.participation.vocabulary import AGENT_REFUSAL_OF_INVITE_REFUSAL, AgentRefusal

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from hopin.participation.group_reads import GroupsRead
    from hopin.participation.live_agents import LiveAgent, LiveAgents
    from hopin.participation.memberships import SeatViews
    from hopin.participation.participants import Person
    from hopin.participation.places import Places
    from hopin.signaling.frames import ForeignFrame, PeerId
    from hopin.signaling.transition import RoomView, Transition


class Offers:
    """The seats' membership in the agent adders group and the offers each seat was last sent, kept in their seat
    views."""

    def __init__(
        self,
        places: Places,
        listens_to_standing: Callable[[Person], bool],
        is_working: Callable[[str], bool],
        seat_views: SeatViews,
    ) -> None:
        self._places = places
        self._listens_to_standing = listens_to_standing
        self._is_working = is_working
        self._seat_views = seat_views
        self.group_reads = GroupReads()

    def keep(self, read: GroupsRead, groups: frozenset[str] | None, room_id: str) -> Membership:
        """Keep the membership a read found, which the act the read was for decides on."""
        membership = membership_in(groups, AGENT_ADDERS_GROUP)
        self._seat_views.of(read.seat, room_id).memberships[AGENT_ADDERS_GROUP] = membership
        return membership

    def send(self, view: RoomView, transition: Transition, agents: LiveAgents, *, read: bool = True) -> None:
        """Send each seat the agents it may add, whether its control shows, whether no place is free for an agent and
        the joined agents it may send away, when they differ from what it was last sent. `read` starts the reads of
        memberships still unknown or failed; the read that just ended asks for none, so a failure retries at the next
        trigger."""
        if view.room_id is None:  # the room has ended
            return
        if read:
            self._read_groups(view, transition)
        full = not view.has_free_place
        for peer_id in view.members:
            seat = self._seat_views.of(peer_id, view.room_id)
            offers = self._offers_for(view, peer_id, agents)
            shown = self._control_shown_to(view, peer_id, offers)
            send_away = self._send_away_of(view, peer_id)
            if (offers, shown, full, send_away) != (seat.offered, seat.shown, seat.full, seat.send_away):
                seat.offered = offers
                seat.shown = shown
                seat.full = full
                seat.send_away = send_away
                transition.send(
                    peer_id,
                    AgentOffersFrame(shown=shown, full=full, offers=list(offers), send_away=list(send_away)).to_json(),
                )

    def on_add(
        self, agents: LiveAgents, peer_id: PeerId, frame: ForeignFrame, view: RoomView, transition: Transition
    ) -> None:
        """Refuse an add of an agent no registration holds, or read the adder's groups afresh, which then decides it."""
        agent_key = frame.data.get("agent_key")
        if not isinstance(agent_key, str):
            raise InvalidMessageError("An add needs the agent's key")
        live = None if view.room_id is None else agents.by_key(agent_key)
        if live is None:
            transition.send(peer_id, AgentRefusedFrame(AgentRefusal.GONE).to_json())
            return
        transition.dispatch(self.group_reads.for_add(peer_id, live))

    def on_send_away(
        self, peer_id: PeerId, frame: ForeignFrame, view: RoomView, transition: Transition
    ) -> DialedAgent | None:
        """The joined agent the frame names when the sender may send it away on the facts of the call now; else read
        the sender's groups afresh, which then decides it, for an agent with an account of its own, or refuse a person's
        own agent's send-away. An agent that is not in the sender's room is not sent away."""
        agent_id = frame.data.get("agent_id")
        if not isinstance(agent_id, str):
            raise InvalidMessageError("A removal needs the agent's id")
        agent = next((agent for agent in self._places.joined_in(view.room_id) if agent.agent_id == agent_id), None)
        if agent is None:
            return None
        if self._may_send_away(view, peer_id, agent, None):
            return agent
        if agent.owner is None:
            transition.send(peer_id, AgentRefusedFrame(AgentRefusal.NOT_ALLOWED_TO_SEND_AWAY).to_json())
        else:
            transition.dispatch(self.group_reads.for_send_away(peer_id, agent))
        return None

    def after_send_away_read(
        self, view: RoomView, peer_id: PeerId, agent: DialedAgent, membership: Membership
    ) -> DialedAgent | AgentRefusal | None:
        """The send-away as it stands after the fresh read: the agent when it is to go, nothing when it is gone, or was
        replaced by a new join, since the press, else why the sender is refused. A failed read decides with no
        membership and refuses as unavailable only when that refuses."""
        if self._places.get(agent.person, agent.room_id) != agent:
            return None
        if self._may_send_away(view, peer_id, agent, membership):
            return agent
        return (
            AgentRefusal.SEND_AWAY_UNAVAILABLE
            if membership is Membership.FAILED
            else AgentRefusal.NOT_ALLOWED_TO_SEND_AWAY
        )

    def call(
        self,
        view: RoomView,
        inviter: PeerId,
        live: LiveAgent,
        agents: LiveAgents,
        *,
        inviter_is_member: bool,
        invitation_id: str,
        now: datetime,
    ) -> Invited | AgentRefusal:
        """The call as it stands after the fresh read: gone when the registration it was read for was replaced
        meanwhile, else what the calling standard decides on the facts of the call now."""
        current = agents.by_person.get(live.person)
        if current is None or current.generation != live.generation:
            return AgentRefusal.GONE
        room_id = cast("str", view.room_id)  # the inviter sits in it
        owner_seat = _owner_seat(view, live.owner_subject)
        # An owner with no seat, or two, has no seat to bind to; the standard then refuses on where the owner is.
        bound_to = live.owner_subject if owner_seat is None else owner_seat
        facts = CallFacts(
            inviter=inviter,
            inviter_present=view.room_of(inviter) is not None,
            inviter_is_member=inviter_is_member,
            agent_listens=self._listens_to_standing(live.person),
            agent_seated=self._agent_seated(live, room_id),
            owner=live.owner_subject,
            owner_at=_owner_at(view, live.owner_subject, bound_to),
            bound_to=bound_to,
            seat_free=view.has_free_place,
        )
        decided = invite(self._places.entry(live.person, room_id), facts, invitation_id=invitation_id, now=now)
        return decided if isinstance(decided, Invited) else AGENT_REFUSAL_OF_INVITE_REFUSAL[decided]

    def accept_facts(self, view: RoomView, live: LiveAgent, invitation: OpenInvitation) -> AcceptFacts:
        """What an accept of the invitation reads of the call now: the owner's presence is judged against the seat the
        invitation is bound to."""
        return AcceptFacts(
            holds_lease=True,  # the agent's registration is its lease
            owner=live.owner_subject,
            owner_at=_owner_at(view, live.owner_subject, invitation.bound_to),
            agent_seated=self._agent_seated(live, cast("str", view.room_id)),
            seat_free=view.has_free_place,
        )

    def _agent_seated(self, live: LiveAgent, room_id: str) -> bool:
        return isinstance(self._places.get(live.person, room_id), DialedAgent)

    def _read_groups(self, view: RoomView, transition: Transition) -> None:
        """Read the groups of each seat whose membership in the agent adders group is unknown or failed to read, unless
        a read of them runs."""
        for peer_id in view.members:
            memberships = self._seat_views.of(peer_id, cast("str", view.room_id)).memberships
            read = self.group_reads.for_offers(peer_id)
            if (
                memberships.get(AGENT_ADDERS_GROUP, Membership.FAILED) is Membership.FAILED
                and not self._is_working(read.key)
                and read not in transition.dispatched
            ):
                transition.dispatch(read)

    def _send_away_of(self, view: RoomView, peer_id: PeerId) -> tuple[str, ...]:
        """The ids of the joined agents a seat may send away on what its last read found, which the display never
        waits to refresh."""
        room_id = cast("str", view.room_id)
        membership = self._seat_views.of(peer_id, room_id).memberships.get(AGENT_ADDERS_GROUP)
        return tuple(
            sorted(
                agent.agent_id
                for agent in self._places.joined_in(room_id)
                if self._may_send_away(view, peer_id, agent, membership)
            )
        )

    @staticmethod
    def _may_send_away(view: RoomView, peer_id: PeerId, agent: DialedAgent, membership: Membership | None) -> bool:
        """The calling standard's decision on the facts of the call: a membership counts for an agent with an account of
        its own only; the owner is any seat of its account, and the host's right, or for a person's own agent its
        sponsor's, is the place's own."""
        return send_away_allowed(
            sender_present=view.room_of(peer_id) is not None,
            sender_is_member=agent.owner is not None and membership is Membership.MEMBER,
            sender_is_owner=agent.owner is not None and peer_id in view.seats_in(agent.owner),
            place_right=view.is_host(peer_id) or (agent.owner is None and peer_id == agent.sponsor),
        )

    def _control_shown_to(self, view: RoomView, peer_id: PeerId, offers: Sequence[Offer]) -> bool:
        """The control shows to a seat with an offer, to the call's host and to a member of the agent adders group, even
        with no agent to list; it never waits on a fresh read."""
        if offers:
            return True
        memberships = self._seat_views.of(peer_id, cast("str", view.room_id)).memberships
        return view.is_host(peer_id) or memberships.get(AGENT_ADDERS_GROUP) is Membership.MEMBER

    def _offers_for(self, view: RoomView, peer_id: PeerId, agents: LiveAgents) -> tuple[Offer, ...]:
        """The agents a seat may add: each with a lease that runs and a listen on its standing that is open, or ended
        within its allowance."""
        room_id = cast("str", view.room_id)
        if self._seat_views.of(peer_id, room_id).memberships.get(AGENT_ADDERS_GROUP) is not Membership.MEMBER:
            return ()
        offers = []
        for live in agents.by_person.values():
            if _owner_seat(view, live.owner_subject) is None or not self._listens_to_standing(live.person):
                continue
            place = self._places.get(live.person, room_id)
            if isinstance(place, OpenInvitation) and place.inviter == peer_id:
                offers.append(Offer(live.key, live.name, pending=True))
            elif place is None and view.has_free_place:
                offers.append(Offer(live.key, live.name, pending=False))
        return tuple(offers)


def _owner_at(view: RoomView, owner_subject: str, bound_to: str) -> OwnerAt:
    """Where the owner is by the call's presence rule: at the one seat the invitation is or will be bound to, away when
    that seat waits to resume, elsewhere on any other seats, and absent on none."""
    if not view.seats_in(owner_subject):
        return OwnerAt.ABSENT
    seat = _owner_seat(view, owner_subject)
    if seat is None or seat != bound_to:
        return OwnerAt.ELSEWHERE
    return OwnerAt.AWAY if view.is_parked(seat) else OwnerAt.AT_BINDING


def _owner_seat(view: RoomView, owner_subject: str) -> PeerId | None:
    """The one seat the owner holds in the call, or none when it holds no seat or two."""
    seats = view.seats_in(owner_subject)
    return seats[0] if len(seats) == 1 else None
