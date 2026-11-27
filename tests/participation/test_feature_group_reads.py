import pytest
from agents.member import LEASE_SECONDS

from hopin.config.constants import SignalType
from hopin.participation.memberships import AGENT_ADDERS_GROUP
from hopin.participation.vocabulary import AgentRefusal, ParticipationSignal
from tests.participation.support.own_accounts import (
    AGENT,
    OWNER,
    HeldRead,
    acceptance_of,
    agent_calls_and_listens,
    let_reads_finish,
    let_reads_start,
    offers_sent_to,
)
from tests.signaling.support.browsers import Browser
from tests.support.signaling_fakes import account_named, text_frame

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class TestAGroupReadThatIsStillRunning:
    async def test_it_shows_nothing_to_a_seat_that_left_meanwhile(
        self, link, participation, groups_of_seat, group_reads, listeners
    ):
        owner = await link.join(OWNER)
        groups_of_seat["guest"] = release = HeldRead()
        member = await link.join("guest", owner.room_id)
        await agent_calls_and_listens(participation, listeners)
        await let_reads_start(group_reads, 1)

        await member.drop(code=1000)
        release.set()
        await let_reads_finish()

        assert offers_sent_to(member) is None

    async def test_it_is_cancelled_with_the_participation_that_shuts_down(
        self, link, participation, groups_of_seat, group_reads, listeners
    ):
        owner = await link.join(OWNER)
        groups_of_seat["guest"] = release = HeldRead()
        member = await link.join("guest", owner.room_id)
        await agent_calls_and_listens(participation, listeners)
        await let_reads_start(group_reads, 1)

        await link.handler.shut_down()
        release.set()
        await let_reads_finish()

        assert offers_sent_to(member) is None


class TestAnAddWhoseFreshReadIsStillRunning:
    async def add_with_a_held_read(self, link, participation, groups_of_seat, group_reads, listeners):
        owner = await link.join(OWNER)
        groups_of_seat["guest"] = frozenset({AGENT_ADDERS_GROUP})
        member = await link.join("guest", owner.room_id)
        await agent_calls_and_listens(participation, listeners)
        await let_reads_finish()
        [offer] = offers_sent_to(member)
        groups_of_seat["guest"] = release = HeldRead()
        member.sent_count += 1
        add = {"type": ParticipationSignal.AGENT_ADD.value, "agent_key": offer["agent_key"]}
        reads_before = len(group_reads)
        member.socket.push(text_frame(add, seq=member.sent_count))  # the seat's handler now waits on the read
        await let_reads_start(group_reads, reads_before + 1)
        return owner, member, release

    async def test_it_opens_no_invitation_when_the_call_ends_meanwhile(
        self, link, participation, groups_of_seat, group_reads, listeners
    ):
        owner, member, release = await self.add_with_a_held_read(
            link, participation, groups_of_seat, group_reads, listeners
        )

        await owner.say({"type": SignalType.END_ROOM.value})
        release.set()
        await link.settle(member.socket)
        await let_reads_finish()

        assert (
            participation.standings_of(AGENT),
            member.frames(ParticipationSignal.AGENT_REFUSED),
        ) == (
            (),
            [],
        )

    async def test_it_is_refused_when_the_owners_seat_left_meanwhile(
        self, link, participation, groups_of_seat, group_reads, listeners
    ):
        """CALL-51 (CALLING-21): The call is decided on the call as the fresh read leaves it: an owner with no seat is
        refused."""
        owner, member, release = await self.add_with_a_held_read(
            link, participation, groups_of_seat, group_reads, listeners
        )

        await owner.drop(code=1000)
        release.set()
        await link.settle(member.socket)
        await let_reads_finish()

        assert (
            participation.standings_of(AGENT),
            [frame["reason"] for frame in member.frames(ParticipationSignal.AGENT_REFUSED)],
        ) == ((), [AgentRefusal.NO_SEAT.value])

    async def test_it_binds_the_invitation_to_the_seat_the_owner_holds_when_the_read_ends(
        self, link, participation, groups_of_seat, group_reads, listeners
    ):
        """CALL-51 (CALLING-21): The call is decided on the call as the fresh read leaves it: an owner whose seat was
        replaced meanwhile is at the new one."""
        owner, member, release = await self.add_with_a_held_read(
            link, participation, groups_of_seat, group_reads, listeners
        )

        await owner.drop(code=1000)
        replacement = await link.join(OWNER, owner.room_id)
        release.set()
        await link.settle(member.socket)
        await let_reads_finish()
        [standing] = participation.standings_of(AGENT)
        accepted = await participation.accept(
            AGENT, acceptance_of(owner.room_id, standing.invitation.invitation_id, standing.invitation.epoch), None
        )

        assert (member.frames(ParticipationSignal.AGENT_REFUSED), accepted.sponsor) == ([], replacement.peer_id)

    async def test_it_is_refused_when_the_agents_lease_ran_out_meanwhile(
        self, link, participation, groups_of_seat, scheduler, group_reads, listeners
    ):
        _owner, member, release = await self.add_with_a_held_read(
            link, participation, groups_of_seat, group_reads, listeners
        )

        await scheduler.advance(LEASE_SECONDS)
        release.set()
        await link.settle(member.socket)
        await let_reads_finish()

        reasons = [frame["reason"] for frame in member.frames(ParticipationSignal.AGENT_REFUSED)]
        assert (participation.standings_of(AGENT), reasons) == ((), [AgentRefusal.GONE.value])


class TestReadsThatAreAlreadyRunning:
    async def test_a_seat_whose_groups_are_being_read_is_not_read_again_by_the_next_trigger(
        self, link, participation, groups_of_seat, group_reads, listeners
    ):
        owner = await link.join(OWNER)
        await link.join("guest", owner.room_id)
        groups_of_seat["guest"] = release = HeldRead()
        await agent_calls_and_listens(participation, listeners)
        await let_reads_start(group_reads, 1)

        await link.join("late", owner.room_id)
        release.set()
        await let_reads_finish()

        assert group_reads.count("guest") == 1


class TestAnAddFromASeatInNoCall:
    async def test_it_is_gone(self, link, participation):
        browser = Browser(link, "nobody")
        browser.sockets.append(await link.open(account_named("nobody")))

        await browser.say({"type": ParticipationSignal.AGENT_ADD.value, "agent_key": "any"})

        assert [frame.get("reason") for frame in browser.socket.sent] == [AgentRefusal.GONE.value]
