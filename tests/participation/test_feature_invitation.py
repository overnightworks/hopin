import asyncio

import pytest
import pytest_asyncio
from agent_runtime_client.wait import LEASE_SECONDS

from hopin.config.constants import SignalType
from hopin.participation.feature import DialedAgent
from hopin.participation.places import PlaceState
from hopin.participation.vocabulary import INVITATION_SECONDS, AgentRefusal, ParticipationSignal
from tests.participation.support.own_accounts import (
    ADDERS,
    AGENT,
    NAME,
    OWNER,
    WALL_CLOCK_SECONDS,
    agent_calls,
    let_reads_finish,
    offers_sent_to,
)
from tests.participation.support.people import person_named
from tests.signaling.support.browsers import HeldWrite
from tests.support.signaling_fakes import text_frame

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
        )


@pytest_asyncio.fixture
async def invited(link, participation):
    owner = await link.join(OWNER)
    member = await link.join("guest", owner.room_id)
    await agent_calls(participation)
    await let_reads_finish()
    [offer] = offers_sent_to(member)
    await member.say({"type": ParticipationSignal.AGENT_ADD.value, "agent_key": offer["agent_key"]})
    await let_reads_finish()
    return Invited(owner, member, participation)


def frames_of(browser, signal):
    return browser.frames(signal)


class TestAnInvitation:
    async def test_it_stands_for_the_agent_with_an_epoch_and_the_time_it_ends(self, invited, changes):
        assert invited.standing.state is PlaceState.INVITED
        assert (invited.invitation.epoch, invited.invitation.expires_at) == (0, WALL_CLOCK_SECONDS + INVITATION_SECONDS)
        assert changes[-1:] == [AGENT]

    async def test_accepting_it_puts_the_agent_in_the_call_under_its_name_and_everyone_sees_the_tile(self, invited):
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

    async def test_it_cannot_be_accepted_twice(self, invited):
        await invited.accept()

        assert await invited.accept() is AgentRefusal.STALE_INVITATION

    @pytest.mark.parametrize(
        "mistake",
        [pytest.param({"invitation_id": "another"}, id="another id"), pytest.param({"epoch": 7}, id="another epoch")],
    )
    async def test_it_is_stale_for_an_id_or_an_epoch_that_is_not_its_own(self, invited, mistake):
        assert await invited.accept(**mistake) is AgentRefusal.STALE_INVITATION
        assert [standing.state for standing in invited.participation.standings_of(AGENT)] == [PlaceState.INVITED]

    async def test_it_ends_after_its_lifetime_and_the_adder_may_add_again(self, invited, scheduler, participation):
        await scheduler.advance(INVITATION_SECONDS - 1)
        await agent_calls(participation)  # the agent still waits, so its lease holds
        await scheduler.advance(1)

        assert invited.participation.standings_of(AGENT) == ()
        assert offers_sent_to(invited.member)[0]["pending"] is False

    async def test_it_ends_with_the_lease_of_the_agent_it_was_made_for(self, invited, scheduler):
        await scheduler.advance(LEASE_SECONDS)

        assert (invited.participation.standings_of(AGENT), offers_sent_to(invited.member)) == ((), [])

    async def test_it_is_stale_past_its_deadline_before_its_timer_ran(self, invited, scheduler):
        scheduler.skip(INVITATION_SECONDS)

        assert await invited.accept() is AgentRefusal.STALE_INVITATION

    async def test_it_is_withdrawn_when_the_owner_leaves_and_stays_stale_for_a_seat_that_takes_the_place(
        self, invited, link
    ):
        old = invited.invitation
        await invited.owner.drop(code=1000)
        await link.join(OWNER, invited.owner.room_id)

        assert invited.participation.standings_of(AGENT) == ()
        refusal = await invited.participation.accept(AGENT, invited.owner.room_id, old.invitation_id, old.epoch)
        assert refusal is AgentRefusal.STALE_INVITATION

    async def test_accepting_it_with_the_owner_away_is_sponsor_away(self, invited):
        await invited.owner.drop()

        assert await invited.accept() is AgentRefusal.SPONSOR_AWAY

    async def test_accepting_it_with_the_owner_on_two_seats_is_two_seats(self, invited, link):
        await link.join(OWNER, invited.owner.room_id)

        assert await invited.accept() is AgentRefusal.TWO_SEATS

    async def test_accepting_it_while_the_owners_seat_sponsors_another_agent_is_busy(self, invited, participation):
        await participation.dial_in(person_named(OWNER), invited.owner.room_id, "Codex")

        assert await invited.accept() is AgentRefusal.BUSY

    async def test_the_owner_may_decline_it_by_leaving_but_an_invited_place_has_nothing_to_leave(self, invited):
        assert await invited.participation.leave(AGENT, invited.owner.room_id) is AgentRefusal.GONE
        assert [standing.state for standing in invited.participation.standings_of(AGENT)] == [PlaceState.INVITED]


class TestAnAgentInTheCall:
    async def test_it_leaves_when_it_calls_leave_and_the_room_sees_the_tile_go(self, invited):
        await invited.accept()

        assert await invited.participation.leave(AGENT, invited.owner.room_id) is None
        assert invited.participation.standings_of(AGENT) == ()
        assert len(frames_of(invited.member, ParticipationSignal.AGENT_LEFT)) == 1

    async def test_its_sponsor_removes_it_and_an_old_accept_is_stale(self, invited):
        old = invited.invitation
        agent = await invited.accept()

        await invited.owner.say({"type": ParticipationSignal.AGENT_REMOVE.value, "agent_id": agent.agent_id})

        assert invited.participation.standings_of(AGENT) == ()
        refusal = await invited.participation.accept(AGENT, invited.owner.room_id, old.invitation_id, old.epoch)
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

    async def test_it_leaves_with_its_owners_seat_for_good_and_its_waiters_are_told(self, invited, changes):
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
