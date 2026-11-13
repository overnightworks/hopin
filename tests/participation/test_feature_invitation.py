import asyncio

import pytest
import pytest_asyncio
from agents.member import INVITATION_SECONDS, LEASE_SECONDS

from hopin.config.constants import SignalType
from hopin.participation.feature import DialedAgent
from hopin.participation.places import PlaceState
from hopin.participation.vocabulary import AgentRefusal, InvitationOutcome, ParticipationSignal
from tests.participation.support.own_accounts import (
    ADDERS,
    AGENT,
    NAME,
    OWNER,
    WALL_CLOCK_SECONDS,
    agent_calls,
    agent_calls_and_listens,
    agent_stops_listening,
    let_reads_finish,
    offers_sent_to,
)
from tests.participation.support.people import person_named
from tests.signaling.support.browsers import HeldWrite
from tests.support.signaling_fakes import ROOM_LIMIT, text_frame

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def adders():
    return {OWNER: ADDERS}


@pytest.fixture(autouse=True)
def members(groups_of_seat):
    groups_of_seat["guest"] = frozenset({ADDERS})


class Invited:
    """An agent invited into a call: the owner hosts it, a member added it, and the agent holds the invitation."""

    def __init__(self, owner, member, participation):
        self.owner, self.member, self.participation = owner, member, participation
        self.agent = None
        [self.standing] = participation.standings_of(AGENT)

    @property
    def invitation(self):
        return self.standing.invitation

    async def accept(self, *, invitation_id=None, epoch=None):
        invitation = self.invitation
        return await self.participation.accept(
            AGENT,
            self.owner.room_id,
            invitation.invitation_id if invitation_id is None else invitation_id,
            invitation.epoch if epoch is None else epoch,
            None,
        )


async def add_the_offered_agent(member):
    [offer] = offers_sent_to(member)
    await member.say({"type": ParticipationSignal.AGENT_ADD.value, "agent_key": offer["agent_key"]})
    await let_reads_finish()


@pytest_asyncio.fixture
async def invited(link, participation, listeners):
    owner = await link.join(OWNER)
    member = await link.join("guest", owner.room_id)
    await agent_calls_and_listens(participation, listeners)
    await let_reads_finish()
    await add_the_offered_agent(member)
    return Invited(owner, member, participation)


def frames_of(browser, signal):
    return browser.frames(signal)


class TestAnInvitation:
    async def test_it_stands_for_the_agent_with_an_epoch_and_the_time_it_ends(self, invited, changes):
        assert invited.standing.state is PlaceState.INVITED
        assert (invited.invitation.epoch, invited.invitation.expires_at) == (0, WALL_CLOCK_SECONDS + INVITATION_SECONDS)
        assert changes[-1:] == [(AGENT, (PlaceState.INVITED,))]

    async def test_accepting_it_puts_the_agent_in_the_call_under_its_name_and_everyone_sees_the_tile(self, invited):
        """CALL-52: The agent accepts the invitation and shows as a tile under its own name."""
        agent = await invited.accept()

        assert isinstance(agent, DialedAgent)
        assert (agent.name, agent.sponsor) == (NAME, invited.owner.peer_id)
        for browser in (invited.owner, invited.member):
            [joined] = frames_of(browser, ParticipationSignal.AGENT_JOINED)
            assert (joined["name"], joined["sponsor"], joined["sponsor_id"]) == (NAME, OWNER, invited.owner.peer_id)
        assert [standing.state for standing in invited.participation.standings_of(AGENT)] == [PlaceState.ACTIVE]

    async def test_accepting_it_withdraws_the_offer_from_every_seat(self, invited):
        await invited.accept()

        assert offers_sent_to(invited.member) == []

    async def test_an_agent_whose_listen_has_ended_still_accepts_and_is_joined(self, invited, listeners):
        """CALL-51: An accept checks the agent's lease, not its listen."""
        await agent_stops_listening(invited.participation, listeners)
        await let_reads_finish()

        agent = await invited.accept()

        assert isinstance(agent, DialedAgent)
        assert [standing.state for standing in invited.participation.standings_of(AGENT)] == [PlaceState.ACTIVE]

    async def test_it_cannot_be_accepted_twice(self, invited):
        await invited.accept()

        assert await invited.accept() is AgentRefusal.STALE_INVITATION

    @pytest.mark.parametrize(
        "mistake",
        [pytest.param({"invitation_id": "another"}, id="another id"), pytest.param({"epoch": 7}, id="another epoch")],
    )
    async def test_it_is_stale_for_an_id_or_an_epoch_that_is_not_its_own(self, invited, mistake):
        """CALL-52: An accept with another invitation or epoch changes nothing."""
        assert await invited.accept(**mistake) is AgentRefusal.STALE_INVITATION
        assert [standing.state for standing in invited.participation.standings_of(AGENT)] == [PlaceState.INVITED]

    async def test_it_ends_after_its_lifetime_and_the_adder_may_add_again(
        self, invited, scheduler, participation, changes, listeners
    ):
        """CALL-52: The invitation lasts the library's INVITATION_SECONDS."""
        await scheduler.advance(INVITATION_SECONDS - 1)
        await agent_calls_and_listens(participation, listeners)  # the agent keeps calling, so its lease holds
        told = len(changes)
        await scheduler.advance(1)

        assert invited.participation.standings_of(AGENT) == ()
        assert offers_sent_to(invited.member)[0]["pending"] is False
        assert len(changes) > told

    async def test_its_expiry_tells_only_the_adder_that_nobody_answered_after_the_offers_list_the_agent_again(
        self, invited, scheduler, participation, listeners
    ):
        """CALL-52: An invitation that runs out tells the seat that made it, and no other, that nobody answered."""
        [offer] = offers_sent_to(invited.member)
        await scheduler.advance(INVITATION_SECONDS - 1)
        await agent_calls_and_listens(participation, listeners)  # the agent keeps calling, so its lease holds
        await scheduler.advance(1)

        told = invited.member.frames(ParticipationSignal.AGENT_OFFERS, ParticipationSignal.AGENT_INVITATION_ENDED)
        assert (
            [frame["type"] for frame in told[-2:]],
            told[-1].get("outcome"),
            told[-1].get("agent_key") == offer["agent_key"],
            invited.owner.frames(ParticipationSignal.AGENT_INVITATION_ENDED),
        ) == (
            [ParticipationSignal.AGENT_OFFERS.value, ParticipationSignal.AGENT_INVITATION_ENDED.value],
            InvitationOutcome.NO_ANSWER.value,
            True,
            [],
        )

    async def test_its_expiry_tells_the_adder_even_when_its_agents_lease_ends_while_the_offers_go_out(
        self, invited, scheduler, participation
    ):
        [offer] = offers_sent_to(invited.member)
        await scheduler.advance(INVITATION_SECONDS - 1)
        await agent_calls(participation)  # the lease now outlives the invitation
        held = HeldWrite(invited.member.socket, first_only=True)
        expiry = asyncio.create_task(scheduler.advance(1))
        await held.reached()

        await scheduler.advance(LEASE_SECONDS)
        held.release.set()
        await expiry

        [ended] = invited.member.frames(ParticipationSignal.AGENT_INVITATION_ENDED)
        assert (ended["outcome"], ended["agent_key"]) == (InvitationOutcome.NO_ANSWER.value, offer["agent_key"])

    async def test_it_ends_with_the_lease_of_the_agent_it_was_made_for(self, invited, scheduler):
        await scheduler.advance(LEASE_SECONDS)

        assert (invited.participation.standings_of(AGENT), offers_sent_to(invited.member)) == ((), [])

    async def test_it_is_stale_past_its_deadline_before_its_timer_ran(self, invited, scheduler):
        scheduler.skip(INVITATION_SECONDS)

        assert await invited.accept() is AgentRefusal.STALE_INVITATION

    async def test_it_is_withdrawn_when_the_owner_leaves_and_stays_stale_for_a_seat_that_takes_the_place(
        self, invited, link, changes
    ):
        """CALL-52: It ends when the owner's seat leaves for good, and an accept after it is stale."""
        old = invited.invitation
        told = len(changes)
        await invited.owner.drop(code=1000)
        await link.join(OWNER, invited.owner.room_id)

        assert invited.participation.standings_of(AGENT) == ()
        assert len(changes) > told
        refusal = await invited.participation.accept(AGENT, invited.owner.room_id, old.invitation_id, old.epoch, None)
        assert refusal is AgentRefusal.STALE_INVITATION

    async def test_accepting_it_with_the_owner_away_is_sponsor_away(self, invited):
        await invited.owner.drop()

        assert await invited.accept() is AgentRefusal.SPONSOR_AWAY

    async def test_accepting_it_with_the_owner_on_two_seats_is_two_seats(self, invited, link):
        await link.join(OWNER, invited.owner.room_id)

        assert await invited.accept() is AgentRefusal.TWO_SEATS

    async def test_accepting_it_while_the_owners_seat_sponsors_another_agent_joins_both(self, invited, participation):
        """CALL-51: A seat sponsors up to the call's two agents."""
        other = await participation.dial_in(person_named(OWNER), invited.owner.room_id, "Codex")

        agent = await invited.accept()

        assert (isinstance(agent, DialedAgent), agent.sponsor, other.sponsor) == (
            True,
            invited.owner.peer_id,
            invited.owner.peer_id,
        )

    async def test_accepting_it_into_a_call_with_no_place_free_is_busy(self, invited, link):
        for number in range(ROOM_LIMIT - 2):
            await link.join(f"person{number}", invited.owner.room_id)

        assert await invited.accept() is AgentRefusal.BUSY

    async def test_it_stays_invited_while_its_owners_seat_parks_and_resumes_and_its_standing_signals_both(
        self, invited, changes
    ):
        told = len(changes)

        await invited.owner.drop()
        after_the_park = len(changes)
        states_while_parked = [standing.state for standing in invited.participation.standings_of(AGENT)]
        await invited.owner.resume()

        assert told < after_the_park < len(changes)
        assert states_while_parked == [PlaceState.INVITED]
        assert [standing.state for standing in invited.participation.standings_of(AGENT)] == [PlaceState.INVITED]

    async def test_a_leave_on_an_invited_place_is_gone_and_the_invitation_stays(self, invited):
        assert await invited.participation.leave(AGENT, invited.owner.room_id) is AgentRefusal.GONE
        assert [standing.state for standing in invited.participation.standings_of(AGENT)] == [PlaceState.INVITED]


class TestAnAgentInTheCall:
    async def test_it_leaves_when_it_calls_leave_and_the_room_sees_the_tile_go(self, invited):
        await invited.accept()

        assert await invited.participation.leave(AGENT, invited.owner.room_id) is None
        assert invited.participation.standings_of(AGENT) == ()
        assert len(frames_of(invited.member, ParticipationSignal.AGENT_LEFT)) == 1

    async def test_an_agent_accepted_again_after_it_left_is_a_tile_under_another_id(self, invited):
        await invited.accept()
        await invited.participation.leave(AGENT, invited.owner.room_id)
        await add_the_offered_agent(invited.member)

        await Invited(invited.owner, invited.member, invited.participation).accept()

        joined = frames_of(invited.member, ParticipationSignal.AGENT_JOINED)
        left = frames_of(invited.member, ParticipationSignal.AGENT_LEFT)
        assert joined[0]["agent_id"] == left[0]["agent_id"] != joined[1]["agent_id"]

    async def test_its_sponsor_removes_it_and_an_old_accept_is_stale(self, invited):
        old = invited.invitation
        agent = await invited.accept()

        await invited.owner.say({"type": ParticipationSignal.AGENT_REMOVE.value, "agent_id": agent.agent_id})

        assert invited.participation.standings_of(AGENT) == ()
        refusal = await invited.participation.accept(AGENT, invited.owner.room_id, old.invitation_id, old.epoch, None)
        assert refusal is AgentRefusal.STALE_INVITATION

    async def test_the_host_removes_it_but_another_guest_is_refused(self, invited, link):
        agent = await invited.accept()
        bystander = await link.join("bystander", invited.owner.room_id)

        await bystander.say({"type": ParticipationSignal.AGENT_REMOVE.value, "agent_id": agent.agent_id})
        refused = [frame["reason"] for frame in bystander.frames(ParticipationSignal.AGENT_REFUSED)]
        still_there = len(invited.participation.standings_of(AGENT))
        await invited.owner.say({"type": ParticipationSignal.AGENT_REMOVE.value, "agent_id": agent.agent_id})

        assert (refused, still_there, invited.participation.standings_of(AGENT)) == (
            [AgentRefusal.NOT_SPONSOR_OR_HOST.value],
            1,
            (),
        )

    async def test_it_leaves_with_its_owners_seat_for_good_and_its_standing_signals(self, invited, changes):
        await invited.accept()
        told = len(changes)

        await invited.owner.drop(code=1000)

        assert (invited.participation.standings_of(AGENT), len(changes) > told) == ((), True)
        assert len(frames_of(invited.member, ParticipationSignal.AGENT_LEFT)) == 1

    async def test_it_is_paused_while_its_owners_seat_is_parked_and_active_again_after_the_resume(
        self, invited, changes
    ):
        await invited.accept()
        told = len(changes)

        await invited.owner.drop()
        paused = [standing.state for standing in invited.participation.standings_of(AGENT)]
        after_the_park = len(changes)
        await invited.owner.resume()
        active = [standing.state for standing in invited.participation.standings_of(AGENT)]

        assert (paused, active) == ([PlaceState.PAUSED], [PlaceState.ACTIVE])
        assert told < after_the_park < len(changes)

    async def test_its_place_ends_with_its_lease_and_the_room_sees_the_tile_go(self, invited, scheduler):
        await invited.accept()

        await scheduler.advance(LEASE_SECONDS)

        assert invited.participation.standings_of(AGENT) == ()
        assert len(frames_of(invited.member, ParticipationSignal.AGENT_LEFT)) == 1

    async def test_a_call_that_ends_leaves_no_standing(self, invited):
        await invited.accept()

        await invited.owner.say({"type": SignalType.END_ROOM.value})

        assert invited.participation.standings_of(AGENT) == ()

    async def test_a_removal_that_a_room_end_overtakes_tells_no_seat_of_the_ended_room(self, invited, link):
        await invited.accept()
        bystander = await link.join("bystander", invited.owner.room_id)
        held = HeldWrite(invited.member.socket)

        removal = asyncio.create_task(invited.participation.leave(AGENT, invited.owner.room_id))
        await held.reached()
        invited.owner.sent_count += 1
        invited.owner.socket.push(text_frame({"type": SignalType.END_ROOM.value}, seq=invited.owner.sent_count))
        await invited.owner.socket.wait_until(lambda: invited.owner.frames(SignalType.ROOM_CLOSED))
        held.release.set()
        await removal

        assert bystander.frames(ParticipationSignal.AGENT_LEFT) == []


class Transitions:
    """The changes of a standing, each as an action on an invited agent; `prepare` runs first and is not judged."""

    @staticmethod
    async def accepted(invited, scheduler):
        invited.agent = await invited.accept()

    @staticmethod
    async def parked(invited, scheduler):
        await Transitions.accepted(invited, scheduler)
        await invited.owner.drop()

    @staticmethod
    async def accept(invited, scheduler):
        await invited.accept()

    @staticmethod
    async def withdrawal(invited, scheduler):
        await invited.owner.drop(code=1000)

    @staticmethod
    async def expiry(invited, scheduler):
        await scheduler.advance(INVITATION_SECONDS - 1)
        await agent_calls(invited.participation)  # the agent keeps calling, so its lease holds
        await scheduler.advance(1)

    @staticmethod
    async def parking(invited, scheduler):
        await invited.owner.drop()

    @staticmethod
    async def resuming(invited, scheduler):
        await invited.owner.resume()

    @staticmethod
    async def leaving(invited, scheduler):
        await invited.participation.leave(AGENT, invited.owner.room_id)

    @staticmethod
    async def host_removal(invited, scheduler):
        await invited.owner.say({"type": ParticipationSignal.AGENT_REMOVE.value, "agent_id": invited.agent.agent_id})

    @staticmethod
    async def room_end(invited, scheduler):
        await invited.owner.say({"type": SignalType.END_ROOM.value})

    @staticmethod
    async def lease_end(invited, scheduler):
        await scheduler.advance(LEASE_SECONDS)

    @staticmethod
    async def another_owner(invited, scheduler):
        await agent_calls(invited.participation, owner="another-owner")


T = Transitions
SIGNALLING_TRANSITIONS = {
    "accept": (None, T.accept, (PlaceState.ACTIVE,)),
    "withdrawal": (None, T.withdrawal, ()),
    "expiry": (None, T.expiry, ()),
    "parking": (T.accepted, T.parking, (PlaceState.PAUSED,)),
    "resuming": (T.parked, T.resuming, (PlaceState.ACTIVE,)),
    "leaving": (T.accepted, T.leaving, ()),
    "host_removal": (T.accepted, T.host_removal, ()),
    "room_end": (T.accepted, T.room_end, ()),
    "lease_end": (T.accepted, T.lease_end, ()),
    "another_owner": (T.accepted, T.another_owner, ()),
}


class TestTheStandingSignal:
    @pytest.mark.parametrize(
        ("prepare", "act", "states"), SIGNALLING_TRANSITIONS.values(), ids=SIGNALLING_TRANSITIONS.keys()
    )
    async def test_each_transition_signals_with_the_new_standing_already_readable(
        self, invited, scheduler, changes, prepare, act, states
    ):
        if prepare is not None:
            await prepare(invited, scheduler)
        told = len(changes)

        await act(invited, scheduler)

        assert [change.person for change in changes[told:]][-1:] == [AGENT]
        assert changes[-1].states == states
