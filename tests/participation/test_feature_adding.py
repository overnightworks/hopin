import pytest

from hopin.config.constants import ErrorReason, SignalType
from hopin.participation.vocabulary import AgentRefusal, ParticipationSignal
from tests.participation.support.own_accounts import (
    ADDERS,
    AGENT,
    NAME,
    OWNER,
    agent_calls,
    let_reads_finish,
    offers_sent_to,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def adders():
    return {OWNER: ADDERS}


@pytest.fixture
def members(groups_of_seat):
    """The seat of `guest` belongs to the adders group, the seat of `stranger` does not."""
    groups_of_seat["guest"] = frozenset({ADDERS})
    groups_of_seat["stranger"] = frozenset({"/apps/hopin"})


async def owner_and_member(link):
    """The owner hosts a call that a member of the adders group has joined."""
    owner = await link.join(OWNER)
    member = await link.join("guest", owner.room_id)
    return owner, member


def refusals_sent_to(browser):
    return [frame["reason"] for frame in browser.frames(ParticipationSignal.AGENT_REFUSED)]


async def add(browser, key):
    await browser.say({"type": ParticipationSignal.AGENT_ADD.value, "agent_key": key})
    await let_reads_finish()


class TestWhoIsOfferedTheAgent:
    @pytest.mark.usefixtures("members")
    async def test_a_member_of_the_adders_group_is_offered_the_agent_while_its_owner_sits_in_the_call(
        self, link, participation
    ):
        _owner, member = await owner_and_member(link)

        await agent_calls(participation)
        await let_reads_finish()

        [offer] = offers_sent_to(member)
        assert (offer["name"], offer["pending"]) == (NAME, False)

    @pytest.mark.usefixtures("members")
    async def test_a_seat_outside_the_adders_group_is_offered_nothing(self, link, participation):
        owner = await link.join(OWNER)
        stranger = await link.join("stranger", owner.room_id)

        await agent_calls(participation)
        await let_reads_finish()

        assert offers_sent_to(stranger) is None

    @pytest.mark.usefixtures("members")
    async def test_nobody_is_offered_the_agent_in_a_call_its_owner_is_not_in(self, link, participation):
        elsewhere = await link.join("guest")

        await agent_calls(participation)
        await let_reads_finish()

        assert offers_sent_to(elsewhere) is None

    @pytest.mark.usefixtures("members")
    async def test_the_offer_goes_when_the_owner_leaves_the_call(self, link, participation):
        owner, member = await owner_and_member(link)
        await agent_calls(participation)
        await let_reads_finish()

        await owner.drop(code=1000)

        assert offers_sent_to(member) == []

    @pytest.mark.usefixtures("members")
    async def test_the_agent_is_offered_to_no_seat_when_no_group_is_configured(
        self, link, participation, adders, group_reads
    ):
        adders.clear()
        _owner, member = await owner_and_member(link)

        await agent_calls(participation)
        await let_reads_finish()

        assert (offers_sent_to(member), group_reads) == (None, [])

    @pytest.mark.usefixtures("members")
    async def test_a_group_read_that_fails_offers_nothing_and_asks_again_at_the_next_offer_trigger(
        self, link, participation, groups_of_seat, group_reads
    ):
        owner, member = await owner_and_member(link)
        groups_of_seat["guest"] = None
        await agent_calls(participation)
        await let_reads_finish()
        reads_before = group_reads.count("guest")
        offers_before = offers_sent_to(member)

        groups_of_seat["guest"] = frozenset({ADDERS})
        await link.join("late", owner.room_id)
        await let_reads_finish()

        assert (reads_before, offers_before, offers_sent_to(member)[0]["name"]) == (1, None, NAME)


@pytest.mark.usefixtures("members")
class TestAnAdd:
    async def test_an_add_of_a_non_member_is_not_adder_and_opens_no_invitation(self, link, participation):
        owner = await link.join(OWNER)
        member = await link.join("guest", owner.room_id)
        stranger = await link.join("stranger", owner.room_id)
        await agent_calls(participation)
        await let_reads_finish()
        [offer] = offers_sent_to(member)

        await add(stranger, offer["agent_key"])

        assert refusals_sent_to(stranger) == [AgentRefusal.NOT_ADDER.value]
        assert participation.standings_of(AGENT) == ()

    async def test_an_add_opens_an_invitation_the_adder_sees_pending_and_no_other_member_is_offered(
        self, link, participation, groups_of_seat
    ):
        groups_of_seat["second"] = frozenset({ADDERS})
        owner, member = await owner_and_member(link)
        second = await link.join("second", owner.room_id)
        await agent_calls(participation)
        await let_reads_finish()
        [offer] = offers_sent_to(member)

        await add(member, offer["agent_key"])

        assert [offers_sent_to(member)[0]["pending"], offers_sent_to(second)] == [True, []]
        [standing] = participation.standings_of(AGENT)
        assert standing.state.value == "invited"

    async def test_a_second_add_of_an_invited_agent_is_already_there(self, link, participation):
        _owner, member = await owner_and_member(link)
        await agent_calls(participation)
        await let_reads_finish()
        [offer] = offers_sent_to(member)
        await add(member, offer["agent_key"])

        await add(member, offer["agent_key"])

        assert refusals_sent_to(member) == [AgentRefusal.ALREADY_THERE.value]

    async def test_an_add_with_the_owner_on_two_seats_is_refused_two_seats(self, link, participation):
        owner, member = await owner_and_member(link)
        await agent_calls(participation)
        await let_reads_finish()
        [offer] = offers_sent_to(member)
        await link.join(OWNER, owner.room_id)

        await add(member, offer["agent_key"])

        assert refusals_sent_to(member) == [AgentRefusal.TWO_SEATS.value]

    async def test_an_add_while_the_owner_is_away_is_refused_sponsor_away_and_the_offer_stays(
        self, link, participation
    ):
        owner, member = await owner_and_member(link)
        await agent_calls(participation)
        await let_reads_finish()
        [offer] = offers_sent_to(member)
        await owner.drop()

        await add(member, offer["agent_key"])

        assert (refusals_sent_to(member), len(offers_sent_to(member))) == ([AgentRefusal.SPONSOR_AWAY.value], 1)

    async def test_an_add_of_a_key_no_agent_holds_is_gone(self, link, participation):
        _owner, member = await owner_and_member(link)

        await add(member, "no-such-key")

        assert refusals_sent_to(member) == [AgentRefusal.GONE.value]

    async def test_an_add_while_the_groups_cannot_be_read_is_unavailable_to_the_adder_alone(
        self, link, participation, groups_of_seat
    ):
        owner, member = await owner_and_member(link)
        await agent_calls(participation)
        await let_reads_finish()
        [offer] = offers_sent_to(member)
        groups_of_seat["guest"] = None

        await add(member, offer["agent_key"])

        assert (refusals_sent_to(member), refusals_sent_to(owner)) == ([AgentRefusal.UNAVAILABLE.value], [])

    async def test_an_add_after_the_member_left_the_group_is_not_adder_and_the_offer_goes(
        self, link, participation, groups_of_seat
    ):
        _owner, member = await owner_and_member(link)
        await agent_calls(participation)
        await let_reads_finish()
        [offer] = offers_sent_to(member)
        groups_of_seat["guest"] = frozenset()

        await add(member, offer["agent_key"])

        assert (refusals_sent_to(member), offers_sent_to(member)) == ([AgentRefusal.NOT_ADDER.value], [])

    async def test_an_add_without_a_key_is_an_invalid_message(self, link, participation):
        _owner, member = await owner_and_member(link)

        await member.say({"type": ParticipationSignal.AGENT_ADD.value})

        assert member.frames(SignalType.ERROR)[-1]["reason"] == ErrorReason.INVALID_MESSAGE.value
