"""CALL-62: the sponsor's seeing switch for a joined agent, at participation's real feature and handler and the agents
library's real record."""

import pytest
from agents.mcp_server.hearing import Sense
from agents.member import PlaceRef

from hopin.agent.tools import CALL_KIND
from hopin.participation.vocabulary import ParticipationSignal
from tests.participation.support.ends import (
    end_by_the_host,
    leave_by_the_agent,
    leave_by_the_sponsor,
    remove_by_the_host,
    shut_down,
)
from tests.participation.support.people import person_named, room_of

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

GUEST = person_named("guest")


def switch(agent_id, *, on):
    return {"type": ParticipationSignal.AGENT_SEEING.value, "agent_id": agent_id, "on": on}


def seeing_frames(browser):
    return [(frame["agent_id"], frame["on"]) for frame in browser.frames(ParticipationSignal.AGENT_SEEING)]


def seers(member_hearing, room_id, seat):
    """The agents the record says see the seat."""
    return {
        principal.sub for principal in member_hearing.sensing(PlaceRef(kind=CALL_KIND, id=room_id), seat, Sense.SEEING)
    }


class TestTheDefault:
    async def test_call_62_the_switch_starts_off_and_only_the_sponsor_is_told(self, member_hearing, call):
        """CALL-62: A joined agent's sponsor has the agent's seeing switch, which starts off, and is told so; the other
        seat is told of its own agent's switch alone."""
        host, guest, agent, host_agent = call

        assert (seeing_frames(guest), seeing_frames(host)) == (
            [(agent.agent_id, False)],
            [(host_agent.agent_id, False)],
        )
        assert seers(member_hearing, room_of(host), guest.peer_id) == set()

    @pytest.mark.parametrize("hears", [True, False], ids=["hearing_on", "hearing_off"])
    async def test_call_62_the_switch_starts_off_whether_or_not_the_agent_hears(self, member_hearing, call, hears):
        """CALL-62: Seeing is its own sense: the setting that lets agents hear neither starts the switch on nor takes
        it away."""
        host, guest, agent, _host_agent = call
        assert seeing_frames(guest) == [(agent.agent_id, False)]

        await guest.say(switch(agent.agent_id, on=True))

        assert seeing_frames(guest)[-1] == (agent.agent_id, True)
        assert seers(member_hearing, room_of(host), guest.peer_id) == {GUEST.subject}


class TestTheSwitch:
    @pytest.mark.parametrize("on", [True, False], ids=["on", "off_again"])
    async def test_call_62_the_sponsor_switches_the_agent_and_is_answered_with_the_record(self, call, on):
        """CALL-62: A switch frame from the sponsor sets the switch and is answered with the server's record."""
        _host, guest, agent, _host_agent = call
        await guest.say(switch(agent.agent_id, on=True))

        await guest.say(switch(agent.agent_id, on=on))

        assert seeing_frames(guest)[-1] == (agent.agent_id, on)

    async def test_call_62_the_seeing_switch_leaves_the_hearing_switch_as_it_is(self, hearing_state, call):
        """CALL-62: One switch never moves the other."""
        _host, guest, agent, _host_agent = call
        before = hearing_state(guest)

        await guest.say(switch(agent.agent_id, on=True))

        assert hearing_state(guest) == before


@pytest.fixture
def hearing_state(member_hearing):
    def state(guest):
        room = PlaceRef(kind=CALL_KIND, id=room_of(guest))
        return member_hearing.sensing(room, guest.peer_id, Sense.HEARING)

    return state


class TestTheEnds:
    @pytest.mark.parametrize(
        "end",
        [
            pytest.param(leave_by_the_agent, id="leave"),
            pytest.param(remove_by_the_host, id="removal"),
            pytest.param(leave_by_the_sponsor, id="the_sponsors_seat_leaving"),
            pytest.param(end_by_the_host, id="the_rooms_end"),
            pytest.param(shut_down, id="shutdown"),
        ],
    )
    async def test_call_62_every_exit_path_ends_seeing_at_once(self, participation, member_hearing, call, end):
        """CALL-62: Leave, removal, the sponsor's leaving, the call's end and shutdown each end the switch."""
        host, guest, agent, _host_agent = call
        await guest.say(switch(agent.agent_id, on=True))

        await end(participation, host, guest, agent)

        assert seers(member_hearing, room_of(host), guest.peer_id) == set()

    async def test_call_62_a_rejoined_agent_starts_off_again(self, participation, call):
        """CALL-62: A switch turned on does not outlive the agent's leave: its next join starts off."""
        host, guest, agent, _host_agent = call
        await guest.say(switch(agent.agent_id, on=True))
        await participation.leave(GUEST, room_of(host))

        rejoined = await participation.dial_in(GUEST, room_of(host), "Claude")

        assert seeing_frames(guest)[-1] == (rejoined.agent_id, False)
