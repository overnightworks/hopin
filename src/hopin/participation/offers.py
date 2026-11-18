"""What each seat of a call may add: the agents it is offered, whether its control shows, and the refusals an add
meets, all decided by the seats' memberships in the owners' adders groups as reads of their groups found them."""

from typing import TYPE_CHECKING, cast

from hopin.participation.frames import AgentOffersFrame, Offer
from hopin.participation.group_reads import GroupReads
from hopin.participation.memberships import Membership, membership_in
from hopin.participation.places import Invited
from hopin.participation.vocabulary import AgentRefusal

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from hopin.participation.group_reads import GroupsRead
    from hopin.participation.live_agents import LiveAgent, LiveAgents
    from hopin.participation.memberships import SeatViews
    from hopin.participation.participants import Person
    from hopin.participation.places import Places
    from hopin.signaling.frames import PeerId
    from hopin.signaling.transition import RoomView, Transition


class Offers:
    """The seats' memberships in the adders groups and the offers each seat was last sent, kept in their seat views."""

    def __init__(
        self,
        places: Places,
        adders: Mapping[str, str],
        listens_to_standing: Callable[[Person], bool],
        is_working: Callable[[str], bool],
        seat_views: SeatViews,
    ) -> None:
        self._places = places
        self._adders = adders
        self._listens_to_standing = listens_to_standing
        self._is_working = is_working
        self._seat_views = seat_views
        self.group_reads = GroupReads()

    def keep(self, read: GroupsRead, groups: frozenset[str] | None, room_id: str) -> Membership | None:
        """Keep the memberships a read found: in every adders group for a read for the offers, which answers nothing;
        in the group of the adding agent's owner for an add, which it answers."""
        memberships = self._seat_views.of(read.seat, room_id).memberships
        if read.adding is None:
            for group in set(self._adders.values()):
                memberships[group] = membership_in(groups, group)
            return None
        group = self._adders[read.adding.agent.owner_subject]
        memberships[group] = membership_in(groups, group)
        return memberships[group]

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
        group = self._adders.get(live.owner_subject)
        if group is None or (
            membership_is_fresh
            and self._seat_views.of(peer_id, room_id).memberships.get(group) is Membership.NOT_MEMBER
        ):
            return AgentRefusal.NOT_ADDER
        if self._places.get(live.person, room_id) is not None:
            return AgentRefusal.ALREADY_THERE
        if not view.has_free_place:
            return AgentRefusal.BUSY
        return None

    def _read_groups(self, view: RoomView, transition: Transition) -> None:
        """Read the groups of each seat whose membership in an adders group is unknown or failed to read, unless a
        read of them runs."""
        groups = set(self._adders.values())
        for peer_id in view.members:
            memberships = self._seat_views.of(peer_id, cast("str", view.room_id)).memberships
            read = self.group_reads.for_offers(peer_id)
            if (
                any(memberships.get(group, Membership.FAILED) is Membership.FAILED for group in groups)
                and not self._is_working(read.key)
                and read not in transition.dispatched
            ):
                transition.dispatch(read)

    def _control_shown_to(self, view: RoomView, peer_id: PeerId, offers: Sequence[Offer]) -> bool:
        """The control shows to a seat with an offer, to the call's host and to a member of an adders group, even with
        no agent to list; it never waits on a fresh read."""
        if offers:
            return True
        memberships = self._seat_views.of(peer_id, cast("str", view.room_id)).memberships
        return view.is_host(peer_id) or any(
            memberships.get(group) is Membership.MEMBER for group in self._adders.values()
        )

    def _offers_for(self, view: RoomView, peer_id: PeerId, agents: LiveAgents) -> tuple[Offer, ...]:
        """The agents a seat may add: each with a lease that runs and a listen on its standing that is open, or ended
        within its allowance."""
        room_id = cast("str", view.room_id)
        memberships = self._seat_views.of(peer_id, room_id).memberships
        offers = []
        for live in agents.by_person.values():
            group = self._adders.get(live.owner_subject)
            sponsor = _sole_seat(view, live.owner_subject)
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


def _sole_seat(view: RoomView, owner_subject: str) -> PeerId | AgentRefusal:
    seats = view.seats_in(owner_subject)
    if not seats:
        return AgentRefusal.NO_SEAT
    if len(seats) > 1:
        return AgentRefusal.TWO_SEATS
    return seats[0]
