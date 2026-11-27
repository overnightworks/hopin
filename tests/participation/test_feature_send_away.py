"""CALL-84: a present seat sends away an agent with an account of its own as the calling standard decides, on a fresh
read of its groups or at once as a seat of the owner's account or the host."""

from dataclasses import dataclass

import pytest
import pytest_asyncio
from agents.member import LEASE_SECONDS

from hopin.participation.memberships import AGENT_ADDERS_GROUP
from hopin.participation.vocabulary import AgentRefusal, ParticipationSignal
from tests.participation.support.own_accounts import (
    AGENT,
    OWNER,
    HeldRead,
    acceptance_of,
    agent_calls,
    agent_calls_and_listens,
    let_reads_finish,
    let_reads_start,
    offers_sent_to,
    send_away_told_to,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

LEFT = ParticipationSignal.AGENT_LEFT
OFFERS = ParticipationSignal.AGENT_OFFERS
REFUSED = ParticipationSignal.AGENT_REFUSED


@pytest.fixture(autouse=True)
def members(groups_of_seat):
    """The seat of `guest` belongs to the adders group; the host, the owner and a bystander do not."""
    groups_of_seat["guest"] = frozenset({AGENT_ADDERS_GROUP})


@dataclass
class Call:
    """The host's call, the owner in it and a member of the adders group who called the owner's agent, which joined."""

    participation: object
    host: object
    owner: object
    member: object
    agent: object
    invitation: object

    @property
    def room_id(self):
        return self.host.room_id

    @property
    def everyone(self):
        return (self.host, self.owner, self.member)

    async def press(self, browser, *, apart_from=None):
        """The seat asks to send the agent away, and every read that runs ends, except the held read of `apart_from`."""
        await browser.say({"type": ParticipationSignal.AGENT_REMOVE.value, "agent_id": self.agent.agent_id})
        await let_reads_finish(apart_from=apart_from)

    async def call_again(self, *, apart_from=None):
        """The member calls the agent again; the new invitation's standing."""
        [offer] = offers_sent_to(self.member)
        await self.member.say({"type": ParticipationSignal.AGENT_ADD.value, "agent_key": offer["agent_key"]})
        await let_reads_finish(apart_from=apart_from)
        [standing] = self.participation.standings_of(AGENT)
        return standing.invitation

    async def accept(self, invitation):
        return await self.participation.accept(
            AGENT, acceptance_of(self.room_id, invitation.invitation_id, invitation.epoch), None
        )

    def left_told_to(self, *browsers):
        return [len(browser.frames(LEFT)) for browser in browsers]


async def joined_call(link, participation, listeners):
    host = await link.join("host")
    owner = await link.join(OWNER, host.room_id)
    member = await link.join("guest", host.room_id)
    await agent_calls_and_listens(participation, listeners)
    await let_reads_finish()
    [offer] = offers_sent_to(member)
    await member.say({"type": ParticipationSignal.AGENT_ADD.value, "agent_key": offer["agent_key"]})
    await let_reads_finish()
    [standing] = participation.standings_of(AGENT)
    scene = Call(participation, host, owner, member, None, standing.invitation)
    scene.agent = await scene.accept(standing.invitation)
    await let_reads_finish()
    return scene


@pytest_asyncio.fixture
async def call(link, participation, listeners):
    return await joined_call(link, participation, listeners)


def refusals_sent_to(browser):
    return [frame["reason"] for frame in browser.frames(REFUSED)]


def kinds_sent_after(browser, count):
    """The kinds of the offers and refusal frames the browser was sent after its first `count`."""
    return [frame["type"] for frame in browser.frames(OFFERS, REFUSED)[count:]]


class TestAMemberSendsTheAgentAway:
    async def test_a_member_the_fresh_read_finds_in_the_group_sends_it_away_and_every_seat_is_told(self, call):
        await call.press(call.member)

        assert (call.left_told_to(*call.everyone), call.participation.standings_of(AGENT)) == ([1, 1, 1], ())

    async def test_an_older_accept_is_stale_and_the_agent_is_called_again_at_once(self, call):
        await call.press(call.member)

        stale = await call.accept(call.invitation)
        again = await call.call_again()

        assert (stale, again.epoch) == (AgentRefusal.STALE_INVITATION, call.invitation.epoch + 1)

    async def test_each_press_reads_the_groups_afresh(self, call, group_reads):
        reads_before = group_reads.count("guest")

        await call.press(call.member)

        assert group_reads.count("guest") == reads_before + 1

    async def test_a_member_whose_groups_were_revoked_since_the_display_is_refused_after_the_offers_frame(
        self, call, groups_of_seat
    ):
        """CALL-84: the display still named the agent; the read at the press finds the member outside the group."""
        assert send_away_told_to(call.member) == [call.agent.agent_id]
        groups_of_seat["guest"] = frozenset()
        seen = len(call.member.frames(OFFERS, REFUSED))

        await call.press(call.member)

        assert (
            kinds_sent_after(call.member, seen),
            refusals_sent_to(call.member),
            send_away_told_to(call.member),
            len(call.participation.standings_of(AGENT)),
        ) == ([OFFERS.value, REFUSED.value], [AgentRefusal.NOT_ALLOWED_TO_SEND_AWAY.value], [], 1)

    async def test_a_failed_read_refuses_send_away_unavailable_and_the_agent_stays(self, call, groups_of_seat):
        groups_of_seat["guest"] = None
        seen = len(call.member.frames(OFFERS, REFUSED))

        await call.press(call.member)

        assert (
            kinds_sent_after(call.member, seen),
            refusals_sent_to(call.member),
            len(call.participation.standings_of(AGENT)),
        ) == ([OFFERS.value, REFUSED.value], [AgentRefusal.SEND_AWAY_UNAVAILABLE.value], 1)

    async def test_a_member_sends_away_an_agent_whose_owners_seat_waits_to_resume(self, call):
        await call.owner.drop()

        await call.press(call.member)

        assert call.participation.standings_of(AGENT) == ()

    async def test_two_presses_give_one_removal_and_the_second_is_quiet(self, call, groups_of_seat):
        groups_of_seat["guest"] = held = HeldRead()
        await call.member.say({"type": ParticipationSignal.AGENT_REMOVE.value, "agent_id": call.agent.agent_id})
        await call.member.say({"type": ParticipationSignal.AGENT_REMOVE.value, "agent_id": call.agent.agent_id})

        held.set()
        await let_reads_finish()

        assert (call.left_told_to(*call.everyone), refusals_sent_to(call.member)) == ([1, 1, 1], [])


class TestTheOwnersAndTheHostsSeats:
    @pytest.mark.parametrize("sender", ["owner", "host"])
    async def test_the_owner_and_the_host_send_it_away_while_reads_fail_and_start_none(
        self, call, groups_of_seat, group_reads, sender
    ):
        browser = getattr(call, sender)
        groups_of_seat[browser.name] = None
        reads_before = len(group_reads)

        await call.press(browser)

        assert (call.participation.standings_of(AGENT), len(group_reads)) == ((), reads_before)

    async def test_a_second_seat_of_the_owners_account_sends_it_away(self, call, link):
        other_tab = await link.join(OWNER, call.room_id)

        await call.press(other_tab)

        assert call.participation.standings_of(AGENT) == ()


class TestASeatThatMayNot:
    async def test_a_seat_that_is_neither_member_owner_nor_host_is_refused_and_the_agent_stays(self, call, link):
        bystander = await link.join("bystander", call.room_id)

        await call.press(bystander)

        assert (
            refusals_sent_to(bystander),
            call.left_told_to(*call.everyone),
            len(call.participation.standings_of(AGENT)),
        ) == ([AgentRefusal.NOT_ALLOWED_TO_SEND_AWAY.value], [0, 0, 0], 1)

    async def test_a_seat_of_another_call_sends_nothing_away_and_reads_nothing(self, call, link, group_reads):
        outsider = await link.join("outsider")
        reads_before = len(group_reads)

        await call.press(outsider)

        assert (
            refusals_sent_to(outsider),
            len(call.participation.standings_of(AGENT)),
            len(group_reads),
        ) == ([], 1, reads_before)


class TestAPressWhoseReadIsStillRunning:
    @pytest_asyncio.fixture
    async def pressing(self, call, link, groups_of_seat, group_reads):
        """A second seat presses while its read, which finds it in the adders group, is held back."""
        second = await link.join("second", call.room_id)
        await let_reads_finish()
        groups_of_seat["second"] = held = HeldRead()
        reads_before = len(group_reads)
        await second.say({"type": ParticipationSignal.AGENT_REMOVE.value, "agent_id": call.agent.agent_id})
        await let_reads_start(group_reads, reads_before + 1)
        return second, held

    async def test_the_sender_that_left_meanwhile_sends_nothing_away(self, call, pressing):
        second, held = pressing

        await second.drop(code=1000)
        held.set()
        await let_reads_finish()

        assert (len(call.participation.standings_of(AGENT)), call.left_told_to(*call.everyone)) == (1, [0, 0, 0])

    async def test_an_agent_gone_when_the_read_answers_is_quiet(self, call, pressing):
        second, held = pressing
        await call.press(call.owner, apart_from=second)

        held.set()
        await let_reads_finish()

        assert (call.left_told_to(call.host, second), refusals_sent_to(second)) == ([1, 1], [])

    async def test_an_agent_that_joined_again_under_a_new_id_is_not_removed_by_the_old_id(self, call, pressing):
        second, held = pressing
        await call.press(call.owner, apart_from=second)
        again = await call.call_again(apart_from=second)
        await call.accept(again)

        held.set()
        await let_reads_finish()

        [standing] = call.participation.standings_of(AGENT)
        assert (standing.state.value, refusals_sent_to(second)) == ("active", [])


@pytest.mark.parametrize("ending", ["sent_away", "leave", "lease_ran_out", "owner_left_for_good"])
async def test_every_ending_of_a_joined_agent_raises_the_places_epoch_by_one(call, scheduler, link, listeners, ending):
    """CALL-84: a send-away writes the standard's row; leave, a lease's end and the owner's departure raise the epoch by
    their own rule: the next invitation is the next epoch."""
    match ending:
        case "sent_away":
            await call.press(call.member)
        case "leave":
            await call.participation.leave(AGENT, call.room_id)
        case "lease_ran_out":
            await scheduler.advance(LEASE_SECONDS)
        case "owner_left_for_good":
            await call.owner.drop(code=1000)
    await agent_calls(call.participation)
    if ending == "owner_left_for_good":
        await link.join(OWNER, call.room_id)
    await let_reads_finish()

    again = await call.call_again()

    assert again.epoch == call.invitation.epoch + 1


class TestWhoIsToldItMayBeSentAway:
    @pytest.mark.parametrize(
        ("who", "sees_it"),
        [("member", True), ("owner", True), ("host", True), ("bystander", False)],
        ids=["member-sees-it", "owner-sees-it", "host-sees-it", "bystander-does-not"],
    )
    async def test_the_offers_frame_names_the_agent_to_each_seat_that_may_send_it_away_and_to_no_other(
        self, call, link, who, sees_it
    ):
        browsers = {
            "member": call.member,
            "owner": call.owner,
            "host": call.host,
            "bystander": await link.join("bystander", call.room_id),
        }
        await let_reads_finish()

        assert (send_away_told_to(browsers[who]) or []) == ([call.agent.agent_id] if sees_it else [])

    @pytest.mark.parametrize("who", ["owner", "host"])
    async def test_a_failed_read_leaves_the_owner_and_the_host_their_send_away(
        self, link, participation, listeners, groups_of_seat, who
    ):
        groups_of_seat[who] = None

        call = await joined_call(link, participation, listeners)

        assert send_away_told_to(getattr(call, who)) == [call.agent.agent_id]

    async def test_a_seat_whose_own_read_failed_is_offered_no_send_away_by_membership(self, call, link, groups_of_seat):
        groups_of_seat["bystander"] = None
        bystander = await link.join("bystander", call.room_id)
        await let_reads_finish()

        assert (send_away_told_to(bystander) or []) == []

    async def test_the_list_follows_the_agents_join_and_its_send_away(self, call):
        after_join = send_away_told_to(call.member)

        await call.press(call.member)

        assert (after_join, send_away_told_to(call.member)) == ([call.agent.agent_id], [])

    async def test_a_frame_goes_when_only_the_send_away_list_changed(self, call):
        """The owner's offers, its control and the full place stay as they were; only its list changes."""
        before = len(call.owner.frames(OFFERS))

        await call.press(call.member)

        assert [frame["send_away"] for frame in call.owner.frames(OFFERS)[before - 1 :]] == [[call.agent.agent_id], []]
