"""An agent's line in the call, at HopIn's real `/mcp` and `/ws`."""

import pytest

from hopin.config.constants import SignalType
from hopin.agent.tools import ACCEPT_INVITATION, JOIN_CALL, LEAVE_CALL, SAY, WAIT
from hopin.participation.vocabulary import AGENT_LINE_MAX_CHARACTERS, AgentRefusal, ParticipationSignal
from tests.support.dialed_call import join_arguments, serving_dialed_call
from tests.support.fake_issuer import FakeIssuer

pytestmark = pytest.mark.integration

OWNER_SUBJECT = "subject-owner"
ADDERS = "/apps/hopin/agent-adders"
NAME = "Claude"
LINE = ParticipationSignal.AGENT_LINE.value
WINDOW_SECONDS = 60
LINES_PER_WINDOW = 10


@pytest.fixture
def own_agent(dialed_call):
    """A person's own agent, joined in its person's call: its token, the person's seat and a second seat."""
    account, _, seat = dialed_call.seated()
    other = dialed_call.seat(dialed_call.member()[1], seat.room_id)
    token = dialed_call.token(account)
    dialed_call.call(token, JOIN_CALL, join_arguments(seat, NAME))
    return token, seat, other


def invited(call, agent_name):
    """The owner in a call, an adder in it, and the owner's agent with an account of its own, invited by the adder."""
    _, owner_login = call.member(subject=OWNER_SUBJECT, groups=(ADDERS,))
    owner = call.seat(owner_login)
    _, adder_login = call.member(groups=(ADDERS,))
    adder = call.seat(adder_login, owner.room_id)
    token = call.agent_token(owner=OWNER_SUBJECT, agent_name=agent_name)
    call.call(token, WAIT, {})
    [offer] = adder.frame_of_type(ParticipationSignal.AGENT_OFFERS.value)["offers"]
    adder.send({"type": ParticipationSignal.AGENT_ADD.value, "agent_key": offer["agent_key"]})
    call.call(token, WAIT, {})
    return owner, adder, token


def accept(call, token, owner):
    [standing] = call.call(token, WAIT, {}).outcome["standings"]
    invitation = standing["invitation"]
    arguments = {"room": owner.room_id, "invitation": invitation["id"], "epoch": invitation["epoch"]}
    call.call(token, ACCEPT_INVITATION, arguments)


def lines_of(seat):
    return [frame for frame in seat.received if frame["type"] == LINE]


def drained(host, *others):
    """Wait until every seat has read what the server had sent before the host's lock, which reaches them all after."""
    host.send({"type": SignalType.LOCK_ROOM.value})
    for seat in (host, *others):
        seat.frame_of_type(SignalType.ROOM_LOCKED.value)


def nothing_read(host, *others):
    drained(host, *others)
    return not any(lines_of(seat) for seat in (host, *others))


def said(dialed_call, token, seat, text):
    return dialed_call.call(token, SAY, {"room": seat.room_id, "text": text})


def refusal_of(refusal):
    return {"refused": refusal.value}


class TestAnAgentWritesALine:
    def test_every_seat_reads_a_line_under_the_name_of_the_agent_that_wrote_it(self, dialed_call, own_agent):
        token, seat, other = own_agent

        dialed = said(dialed_call, token, seat, "  Hello all  ")

        assert (dialed.is_error, dialed.outcome) == (False, {})
        for reader in (seat, other):
            frame = reader.frame_of_type(LINE)
            assert (frame["name"], frame["text"]) == (NAME, "Hello all")
            assert frame["agent_id"]

    def test_an_agent_with_an_account_of_its_own_writes_a_line_every_seat_reads(self, sign_in_settings):
        sign_in = sign_in_settings()
        with serving_dialed_call(sign_in, FakeIssuer(sign_in), {OWNER_SUBJECT: ADDERS}) as call:
            owner, adder, token = invited(call, "Mozart")
            accept(call, token, owner)

            dialed = said(call, token, owner, "Good evening")

            assert (dialed.is_error, dialed.outcome) == (False, {})
            for reader in (owner, adder):
                frame = reader.frame_of_type(LINE)
                assert (frame["name"], frame["text"]) == ("Mozart", "Good evening")

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("", id="empty"),
            pytest.param("   ", id="blank"),
            pytest.param("x" * (AGENT_LINE_MAX_CHARACTERS + 1), id="too long"),
            pytest.param("two\nlines", id="line break"),
            pytest.param("bell\x07", id="control character"),
        ],
    )
    def test_a_line_that_is_no_line_is_refused_and_no_seat_reads_it(self, dialed_call, own_agent, text):
        token, seat, other = own_agent

        dialed = said(dialed_call, token, seat, text)

        assert (dialed.is_error, dialed.outcome) == (True, refusal_of(AgentRefusal.BAD_LINE))
        assert nothing_read(seat, other)

    def test_a_line_of_the_longest_length_is_read(self, dialed_call, own_agent):
        token, seat, _ = own_agent

        dialed = said(dialed_call, token, seat, "x" * AGENT_LINE_MAX_CHARACTERS)

        assert dialed.outcome == {}

    def test_a_line_that_is_no_text_is_refused(self, dialed_call, own_agent):
        token, seat, _ = own_agent

        dialed = dialed_call.call(token, SAY, {"room": seat.room_id, "text": 7})

        assert dialed.outcome == refusal_of(AgentRefusal.BAD_LINE)


class TestTheRateOfLines:
    def test_an_eleventh_line_within_the_window_is_refused_and_no_seat_reads_it(self, dialed_call, own_agent):
        token, seat, other = own_agent
        for number in range(LINES_PER_WINDOW):
            assert said(dialed_call, token, seat, f"line {number}").outcome == {}

        dialed = said(dialed_call, token, seat, "one too many")

        assert (dialed.is_error, dialed.outcome) == (True, refusal_of(AgentRefusal.TOO_FAST))
        drained(seat, other)
        assert [frame["text"] for frame in lines_of(other)] == [f"line {number}" for number in range(LINES_PER_WINDOW)]

    def test_a_line_after_the_window_has_passed_is_read(self, dialed_call, own_agent):
        token, seat, _ = own_agent
        for number in range(LINES_PER_WINDOW):
            said(dialed_call, token, seat, f"line {number}")
        dialed_call.pass_time(WINDOW_SECONDS)

        dialed = said(dialed_call, token, seat, "later")

        assert dialed.outcome == {}

    def test_a_refused_line_does_not_count_against_the_window(self, dialed_call, own_agent):
        token, seat, _ = own_agent
        for _ in range(LINES_PER_WINDOW + 1):
            said(dialed_call, token, seat, "")

        assert said(dialed_call, token, seat, "fine").outcome == {}

    def test_an_agent_that_rejoins_starts_a_new_window(self, dialed_call, own_agent):
        token, seat, _ = own_agent
        for number in range(LINES_PER_WINDOW):
            said(dialed_call, token, seat, f"line {number}")
        dialed_call.call(token, LEAVE_CALL, {"room": seat.room_id})
        dialed_call.call(token, JOIN_CALL, join_arguments(seat, NAME))

        assert said(dialed_call, token, seat, "again").outcome == {}


class TestAnAgentThatIsNotJoined:
    def test_an_agent_that_left_answers_gone_and_no_seat_reads_a_line(self, dialed_call, own_agent):
        token, seat, other = own_agent
        dialed_call.call(token, LEAVE_CALL, {"room": seat.room_id})

        dialed = said(dialed_call, token, seat, "still here?")

        assert (dialed.is_error, dialed.outcome) == (True, refusal_of(AgentRefusal.GONE))
        assert nothing_read(seat, other)

    def test_an_agent_in_another_room_answers_gone(self, dialed_call, own_agent):
        token, seat, _ = own_agent

        dialed = dialed_call.call(token, SAY, {"room": "another-room", "text": "hello"})

        assert dialed.outcome == refusal_of(AgentRefusal.GONE)

    def test_an_agent_that_is_only_invited_answers_gone(self, sign_in_settings):
        sign_in = sign_in_settings()
        with serving_dialed_call(sign_in, FakeIssuer(sign_in), {OWNER_SUBJECT: ADDERS}) as call:
            owner, _, token = invited(call, "Claude")

            dialed = said(call, token, owner, "hello")

            assert dialed.outcome == refusal_of(AgentRefusal.GONE)
            assert nothing_read(owner)
