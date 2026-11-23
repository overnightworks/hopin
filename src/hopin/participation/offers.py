"""What each seat of a call may add: the agents it is offered, whether its control shows, and the facts the calling
standard decides an add on, as the seats' membership in the realm group of agent adders a read of their groups found
it and the call's seats give them."""

from typing import TYPE_CHECKING, cast

from agents.calling import AcceptFacts, CallFacts, Invited, OpenInvitation, OwnerAt, invite

from hopin.participation.frames import AgentOffersFrame, Offer
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
    from hopin.signaling.frames import PeerId
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

    def keep(self, read: GroupsRead, groups: frozenset[str] | None, room_id: str) -> Membership | None:
        """Keep the membership a read found; an add's read answers it, a read for the offers answers nothing."""
        memberships = self._seat_views.of(read.seat, room_id).memberships
        memberships[AGENT_ADDERS_GROUP] = membership_in(groups, AGENT_ADDERS_GROUP)
        return None if read.adding is None else memberships[AGENT_ADDERS_GROUP]

    def send(self, view: RoomView, transition: Transition, agents: LiveAgents, *, read: bool = True) -> None:
        """Send each seat the agents it may add, whether its control shows and whether no place is free for an agent,
        when they differ from what it was last sent. `read` starts the reads of memberships still unknown or failed;
        the read that just ended asks for none, so a failure retries at the next trigger."""
        if view.room_id is None:  # the room has ended
            return
        if read:
            self._read_groups(view, transition)
        full = not view.has_free_place
        for peer_id in view.members:
            seat = self._seat_views.of(peer_id, view.room_id)
            offers = self._offers_for(view, peer_id, agents)
            shown = self._control_shown_to(view, peer_id, offers)
            if (offers, shown, full) != (seat.offered, seat.shown, seat.full):
                seat.offered = offers
                seat.shown = shown
                seat.full = full
                transition.send(peer_id, AgentOffersFrame(shown=shown, full=full, offers=list(offers)).to_json())

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
        seats = view.seats_in(live.owner_subject)
        bound_to = seats[0] if seats else live.owner_subject  # an owner with no seat has nothing to bind to
        facts = CallFacts(
            inviter=inviter,
            inviter_present=view.room_of(inviter) is not None,
            inviter_is_member=inviter_is_member,
            agent_listens=self._listens_to_standing(live.person),
            agent_seated=isinstance(self._places.get(live.person, room_id), DialedAgent),
            owner=live.owner_subject,
            owner_at=owner_at(view, live.owner_subject, bound_to),
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
            owner_at=owner_at(view, live.owner_subject, invitation.bound_to),
            agent_seated=isinstance(self._places.get(live.person, cast("str", view.room_id)), DialedAgent),
            seat_free=view.has_free_place,
        )

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
            if isinstance(_sole_seat(view, live.owner_subject), AgentRefusal) or not self._listens_to_standing(
                live.person
            ):
                continue
            place = self._places.get(live.person, room_id)
            if isinstance(place, OpenInvitation) and place.inviter == peer_id:
                offers.append(Offer(live.key, live.name, pending=True))
            elif place is None and view.has_free_place:
                offers.append(Offer(live.key, live.name, pending=False))
        return tuple(offers)


def owner_at(view: RoomView, owner_subject: str, bound_to: str) -> OwnerAt:
    """Where the owner is by the call's presence rule: at the one seat the invitation is or will be bound to, away when
    that seat waits to resume, elsewhere on any other seats, and absent on none."""
    seats = view.seats_in(owner_subject)
    if not seats:
        return OwnerAt.ABSENT
    if seats != (bound_to,):
        return OwnerAt.ELSEWHERE
    return OwnerAt.AWAY if view.is_parked(seats[0]) else OwnerAt.AT_BINDING


def _sole_seat(view: RoomView, owner_subject: str) -> PeerId | AgentRefusal:
    seats = view.seats_in(owner_subject)
    if not seats:
        return AgentRefusal.NO_SEAT
    if len(seats) > 1:
        return AgentRefusal.TWO_SEATS
    return seats[0]
