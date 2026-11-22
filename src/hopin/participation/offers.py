"""What each seat of a call may add: the agents it is offered, whether its control shows, and the refusals an add
meets, all decided by the seats' membership in the realm group of agent adders as a read of their groups found it."""

from typing import TYPE_CHECKING, cast

from hopin.participation.frames import AgentOffersFrame, Offer
from hopin.participation.group_reads import GroupReads
from hopin.participation.memberships import AGENT_ADDERS_GROUP, Membership, membership_in
from hopin.participation.places import Invited
from hopin.participation.vocabulary import AgentRefusal

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

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

    def refusal_of_add_now(
        self, view: RoomView, peer_id: PeerId, live: LiveAgent, sponsor: PeerId, agents: LiveAgents
    ) -> AgentRefusal | None:
        """The add as it stands after the fresh read: the same registration and the same single seat of the owner."""
        current = agents.by_person.get(live.person)
        if current is None or current.generation != live.generation:
            return AgentRefusal.GONE
        refusal = self.refusal_of_add(view, peer_id, live, membership_is_fresh=True)
        if refusal is None and view.seats_in(live.owner_subject) != (sponsor,):
            return AgentRefusal.NO_SEAT
        return refusal

    def refusal_of_add(
        self, view: RoomView, peer_id: PeerId, live: LiveAgent, *, membership_is_fresh: bool
    ) -> AgentRefusal | None:
        """The refusals an add meets; a membership read before this add decides nothing, only the fresh one does."""
        room_id = cast("str", view.room_id)  # the adder sits in it
        sponsor = _sole_seat(view, live.owner_subject)
        if isinstance(sponsor, AgentRefusal):
            return sponsor
        if view.is_parked(sponsor):
            return AgentRefusal.SPONSOR_AWAY
        if (
            membership_is_fresh
            and self._seat_views.of(peer_id, room_id).memberships.get(AGENT_ADDERS_GROUP) is Membership.NOT_MEMBER
        ):
            return AgentRefusal.NOT_ADDER
        if self._places.get(live.person, room_id) is not None:
            return AgentRefusal.ALREADY_THERE
        if not view.has_free_place:
            return AgentRefusal.BUSY
        return None

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
            if isinstance(place, Invited) and place.adder == peer_id:
                offers.append(Offer(live.key, live.name, pending=True))
            elif place is None and view.has_free_place:
                offers.append(Offer(live.key, live.name, pending=False))
        return tuple(offers)


def _sole_seat(view: RoomView, owner_subject: str) -> PeerId | AgentRefusal:
    seats = view.seats_in(owner_subject)
    if not seats:
        return AgentRefusal.NO_SEAT
    if len(seats) > 1:
        return AgentRefusal.TWO_SEATS
    return seats[0]
