import pytest

from hopin.config.constants import ErrorReason, SignalType
from hopin.participation.vocabulary import AgentRefusal, ParticipationSignal
from tests.participation.support.own_accounts import (
    ADDERS,
    AGENT,
    NAME,
    OWNER,
    agent_calls,
    agent_calls_and_listens,
    agent_listens,
    agent_stops_listening,
    control_shown_to,
    let_reads_finish,
    offers_sent_to,
)
from tests.participation.support.people import person_named
from tests.signaling.support.browsers import clear_sent

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


def kinds_of_frames_sent_to(browser):
    frames = browser.frames(ParticipationSignal.AGENT_OFFERS, ParticipationSignal.AGENT_REFUSED)
    return [frame["type"] for frame in frames]


def refusals_sent_to(browser):
    return [frame["reason"] for frame in browser.frames(ParticipationSignal.AGENT_REFUSED)]


async def add(browser, key):
    await browser.say({"type": ParticipationSignal.AGENT_ADD.value, "agent_key": key})
    await let_reads_finish()


class TestWhoIsOfferedTheAgent:
    @pytest.mark.usefixtures("members")
    async def test_a_member_of_the_adders_group_is_offered_the_agent_while_its_owner_sits_in_the_call(
        self, link, participation, listeners
    ):
        _owner, member = await owner_and_member(link)

        await agent_calls(participation)
        await agent_listens(participation, listeners)
        await let_reads_finish()

        [offer] = offers_sent_to(member)
        assert (offer["name"], offer["pending"]) == (NAME, False)

    @pytest.mark.usefixtures("members")
    async def test_an_agent_with_a_lease_and_no_listen_is_offered_to_nobody(self, link, participation):
        """CALL-51: An agent whose lease runs but which listens on no stream of its standing is offered to no seat."""
        _owner, member = await owner_and_member(link)

        await agent_calls(participation)
        await let_reads_finish()

        assert offers_sent_to(member) == []

    @pytest.mark.usefixtures("members")
    async def test_a_listen_that_ends_without_a_next_one_drops_the_offer_on_every_seat_that_showed_it(
        self, link, participation, listeners, groups_of_seat
    ):
        """CALL-51: A listen that ends without a next one drops the offer on every seat that showed it."""
        groups_of_seat["second"] = frozenset({ADDERS})
        owner = await link.join(OWNER)
        first = await link.join("guest", owner.room_id)
        second = await link.join("second", owner.room_id)
        await agent_calls(participation)
        await agent_listens(participation, listeners)
        await let_reads_finish()
        assert [offer["name"] for offer in offers_sent_to(first) + offers_sent_to(second)] == [NAME, NAME]

        await agent_stops_listening(participation, listeners)
        await let_reads_finish()

        assert (offers_sent_to(first), offers_sent_to(second)) == ([], [])

    @pytest.mark.usefixtures("members")
    async def test_a_seat_outside_the_adders_group_is_offered_nothing(self, link, participation, listeners):
        owner = await link.join(OWNER)
        stranger = await link.join("stranger", owner.room_id)

        await agent_calls_and_listens(participation, listeners)
        await let_reads_finish()

        assert offers_sent_to(stranger) is None

    @pytest.mark.usefixtures("members")
    async def test_nobody_is_offered_the_agent_in_a_call_its_owner_is_not_in(self, link, participation, listeners):
        elsewhere = await link.join("guest")

        await agent_calls_and_listens(participation, listeners)
        await let_reads_finish()

        assert offers_sent_to(elsewhere) == []

    @pytest.mark.usefixtures("members")
    async def test_the_offer_goes_when_the_owner_leaves_the_call(self, link, participation, listeners):
        owner, member = await owner_and_member(link)
        await agent_calls_and_listens(participation, listeners)
        await let_reads_finish()

        await owner.drop(code=1000)

        assert offers_sent_to(member) == []

    @pytest.mark.usefixtures("members")
    async def test_the_agent_is_offered_to_no_seat_when_no_group_is_configured(
        self, link, participation, adders, group_reads, listeners
    ):
        adders.clear()
        _owner, member = await owner_and_member(link)

        await agent_calls_and_listens(participation, listeners)
        await let_reads_finish()

        assert (offers_sent_to(member), group_reads) == (None, [])

    @pytest.mark.usefixtures("members")
    async def test_a_member_of_the_adders_group_and_the_host_are_shown_the_control_while_no_agent_is_listed(self, link):
        """CALL-51: A member of the adders group and the call's host are shown the control with no agent listed."""
        owner = await link.join("host")
        member = await link.join("guest", owner.room_id)
        stranger = await link.join("stranger", owner.room_id)
        await let_reads_finish()

        assert [control_shown_to(seat) for seat in (owner, member, stranger)] == [True, True, None]
        assert [offers_sent_to(seat) for seat in (owner, member)] == [[], []]

    async def test_the_host_is_shown_the_control_when_no_group_is_configured(self, link, adders):
        """CALL-51: The call's host is shown the control even when no group may add an agent."""
        adders.clear()
        owner = await link.join("host")
        await let_reads_finish()

        assert control_shown_to(owner) is True

    @pytest.mark.usefixtures("members")
    async def test_a_group_read_that_fails_offers_nothing_and_asks_again_at_the_next_offer_trigger(
        self, link, participation, groups_of_seat, group_reads, listeners
    ):
        owner = await link.join(OWNER)
        groups_of_seat["guest"] = None
        member = await link.join("guest", owner.room_id)
        await let_reads_finish()
        reads_before = group_reads.count("guest")
        offers_before = offers_sent_to(member)

        groups_of_seat["guest"] = frozenset({ADDERS})
        await agent_calls_and_listens(participation, listeners)
        await let_reads_finish()

        assert (reads_before, offers_before, offers_sent_to(member)[0]["name"]) == (1, None, NAME)


@pytest.mark.usefixtures("members")
class TestAnAdd:
    async def test_an_add_of_a_non_member_is_not_adder_and_opens_no_invitation(self, link, participation, listeners):
        owner = await link.join(OWNER)
        member = await link.join("guest", owner.room_id)
        stranger = await link.join("stranger", owner.room_id)
        await agent_calls_and_listens(participation, listeners)
        await let_reads_finish()
        [offer] = offers_sent_to(member)

        await add(stranger, offer["agent_key"])

        assert refusals_sent_to(stranger) == [AgentRefusal.NOT_ADDER.value]
        assert participation.standings_of(AGENT) == ()

    async def test_an_add_of_a_seat_that_joined_the_group_after_an_earlier_read_is_accepted(
        self, link, participation, groups_of_seat, listeners
    ):
        owner = await link.join(OWNER)
        member = await link.join("guest", owner.room_id)
        stranger = await link.join("stranger", owner.room_id)
        await agent_calls_and_listens(participation, listeners)
        await let_reads_finish()
        [offer] = offers_sent_to(member)
        groups_of_seat["stranger"] = frozenset({ADDERS})

        await add(stranger, offer["agent_key"])

        assert (refusals_sent_to(stranger), len(participation.standings_of(AGENT))) == ([], 1)

    async def test_an_add_after_the_agents_listen_has_ended_is_served_and_opens_the_invitation(
        self, link, participation, listeners
    ):
        """CALL-51: An add checks the agent's lease, not its listen, so an add in a re-listen gap is served."""
        _owner, member = await owner_and_member(link)
        await agent_calls_and_listens(participation, listeners)
        await let_reads_finish()
        [offer] = offers_sent_to(member)
        await agent_stops_listening(participation, listeners)
        await let_reads_finish()

        await add(member, offer["agent_key"])

        assert refusals_sent_to(member) == []
        [standing] = participation.standings_of(AGENT)
        assert standing.state.value == "invited"

    async def test_an_add_opens_an_invitation_the_adder_sees_pending_and_no_other_member_is_offered(
        self, link, participation, groups_of_seat, listeners
    ):
        groups_of_seat["second"] = frozenset({ADDERS})
        owner, member = await owner_and_member(link)
        second = await link.join("second", owner.room_id)
        await agent_calls_and_listens(participation, listeners)
        await let_reads_finish()
        [offer] = offers_sent_to(member)

        await add(member, offer["agent_key"])

        assert [offers_sent_to(member)[0]["pending"], offers_sent_to(second)] == [True, []]
        [standing] = participation.standings_of(AGENT)
        assert standing.state.value == "invited"

    async def test_a_second_add_of_an_invited_agent_is_already_there(self, link, participation, listeners):
        _owner, member = await owner_and_member(link)
        await agent_calls_and_listens(participation, listeners)
        await let_reads_finish()
        [offer] = offers_sent_to(member)
        await add(member, offer["agent_key"])

        await add(member, offer["agent_key"])

        assert refusals_sent_to(member) == [AgentRefusal.ALREADY_THERE.value]

    async def test_an_add_while_the_owners_seat_sponsors_another_agent_opens_an_invitation(
        self, link, participation, listeners
    ):
        """CALL-51: A seat sponsors up to the call's two agents, so the owner's other agent is still offered."""
        owner, member = await owner_and_member(link)
        await agent_calls_and_listens(participation, listeners)
        await participation.dial_in(person_named(OWNER), owner.room_id, "Codex")
        await let_reads_finish()
        [offer] = offers_sent_to(member)

        await add(member, offer["agent_key"])

        assert (refusals_sent_to(member), [standing.state.value for standing in participation.standings_of(AGENT)]) == (
            [],
            ["invited"],
        )

    async def test_an_add_into_a_call_that_holds_two_agents_is_busy_and_the_offer_is_gone(
        self, link, participation, listeners
    ):
        """CALL-51: Place full is shown when the call holds two agents."""
        owner, member = await owner_and_member(link)
        await agent_calls_and_listens(participation, listeners)
        await let_reads_finish()
        [offer] = offers_sent_to(member)
        await participation.dial_in(person_named(OWNER), owner.room_id, "Codex")
        await participation.dial_in(person_named("guest"), owner.room_id, "Gemini")

        await add(member, offer["agent_key"])

        assert (refusals_sent_to(member), offers_sent_to(member)) == ([AgentRefusal.BUSY.value], [])

    async def test_an_add_with_the_owner_on_two_seats_is_refused_two_seats(self, link, participation, listeners):
        owner, member = await owner_and_member(link)
        await agent_calls_and_listens(participation, listeners)
        await let_reads_finish()
        [offer] = offers_sent_to(member)
        await link.join(OWNER, owner.room_id)

        await add(member, offer["agent_key"])

        assert refusals_sent_to(member) == [AgentRefusal.TWO_SEATS.value]

    async def test_an_add_while_the_owner_is_away_is_refused_sponsor_away_and_the_offer_stays(
        self, link, participation, listeners
    ):
        owner, member = await owner_and_member(link)
        await agent_calls_and_listens(participation, listeners)
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
        self, link, participation, groups_of_seat, listeners
    ):
        owner, member = await owner_and_member(link)
        await agent_calls_and_listens(participation, listeners)
        await let_reads_finish()
        [offer] = offers_sent_to(member)
        groups_of_seat["guest"] = None

        await add(member, offer["agent_key"])

        assert (refusals_sent_to(member), refusals_sent_to(owner)) == ([AgentRefusal.UNAVAILABLE.value], [])

    async def test_an_add_after_the_member_left_the_group_is_not_adder_and_the_offer_goes(
        self, link, participation, groups_of_seat, listeners
    ):
        _owner, member = await owner_and_member(link)
        await agent_calls_and_listens(participation, listeners)
        await let_reads_finish()
        [offer] = offers_sent_to(member)
        groups_of_seat["guest"] = frozenset()

        await add(member, offer["agent_key"])

        assert (refusals_sent_to(member), offers_sent_to(member)) == ([AgentRefusal.NOT_ADDER.value], [])

    async def test_a_refused_add_that_changes_the_offers_sends_the_offers_before_the_refusal(
        self, link, participation, groups_of_seat, listeners
    ):
        """CALL-51: A refusal that changes the offers follows the offers frame, so a later offers frame is later."""
        _owner, member = await owner_and_member(link)
        await agent_calls_and_listens(participation, listeners)
        await let_reads_finish()
        [offer] = offers_sent_to(member)
        groups_of_seat["guest"] = frozenset()
        clear_sent(member)

        await add(member, offer["agent_key"])

        assert kinds_of_frames_sent_to(member) == [
            ParticipationSignal.AGENT_OFFERS.value,
            ParticipationSignal.AGENT_REFUSED.value,
        ]

    async def test_a_refused_add_that_changes_no_offers_sends_only_the_refusal(self, link, participation, listeners):
        """CALL-51: A refusal that changes no offers is the only frame the add sends."""
        owner, member = await owner_and_member(link)
        await agent_calls_and_listens(participation, listeners)
        await let_reads_finish()
        [offer] = offers_sent_to(member)
        await owner.drop()
        clear_sent(member)

        await add(member, offer["agent_key"])

        assert kinds_of_frames_sent_to(member) == [ParticipationSignal.AGENT_REFUSED.value]

    async def test_an_add_without_a_key_is_an_invalid_message(self, link, participation):
        _owner, member = await owner_and_member(link)

        await member.say({"type": ParticipationSignal.AGENT_ADD.value})

        assert member.frames(SignalType.ERROR)[-1]["reason"] == ErrorReason.INVALID_MESSAGE.value
