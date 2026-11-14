"""CALL-57, CALL-62 and CALL-63: how participation's real feature and handler take the frames of an agent's switches
and of a look's answer when they come from the wrong seat, name no agent, or are no frame of their form."""

import pytest
from agents.mcp_server.hearing import Sense
from agents.member import PlaceRef

from hopin.agent.tools import CALL_KIND
from hopin.config.constants import ErrorReason, SignalType
from hopin.participation.vocabulary import AgentRefusal, ParticipationSignal
from tests.participation.support.people import person_named, room_of

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

GUEST = person_named("guest")
HEARING = ParticipationSignal.AGENT_HEARING
SEEING = ParticipationSignal.AGENT_SEEING

# Each sense with the frame that switches it and whether it is on for an agent dialed in by its sponsor.
SENSES = [
    pytest.param(HEARING, Sense.HEARING, True, id="hearing"),
    pytest.param(SEEING, Sense.SEEING, False, id="seeing"),
]


@pytest.fixture
def hears():
    return True


def switch(signal, agent_id, *, on):
    return {"type": signal.value, "agent_id": agent_id, "on": on}


def refusals(browser):
    return [frame["reason"] for frame in browser.frames(ParticipationSignal.AGENT_REFUSED)]


def errors(browser):
    return [frame["reason"] for frame in browser.frames(SignalType.ERROR)]


def subjects_with(member_hearing, host, guest, sense):
    """The agents the record says have the sense of the guest's seat."""
    place = PlaceRef(kind=CALL_KIND, id=room_of(host))
    return {principal.sub for principal in member_hearing.sensing(place, guest.peer_id, sense)}


def subjects_at_the_start(starts_on):
    return {GUEST.subject} if starts_on else set()


class TestASwitchOfASense:
    @pytest.mark.parametrize(("signal", "sense", "starts_on"), SENSES)
    async def test_call_57_62_a_seat_that_does_not_sponsor_the_agent_is_refused_and_the_switch_stays(
        self, member_hearing, call, signal, sense, starts_on
    ):
        """CALL-57 and CALL-62: Only the agent's sponsor has its switch; any other seat, the host included, is
        refused."""
        host, guest, agent, _host_agent = call

        await host.say(switch(signal, agent.agent_id, on=not starts_on))

        assert (refusals(host), subjects_with(member_hearing, host, guest, sense)) == (
            [AgentRefusal.NOT_SPONSOR.value],
            subjects_at_the_start(starts_on),
        )

    @pytest.mark.parametrize("signal", [HEARING, SEEING], ids=["hearing", "seeing"])
    async def test_call_57_62_a_switch_of_an_agent_that_is_not_joined_there_is_refused_as_gone(self, call, signal):
        """CALL-57 and CALL-62: A switch frame for an agent that holds no joined place in the seat's room is refused as
        gone."""
        host, _guest, _agent, _host_agent = call

        await host.say(switch(signal, "another-agent", on=True))

        assert refusals(host) == [AgentRefusal.GONE.value]

    @pytest.mark.parametrize(("signal", "sense"), [pytest.param(*param.values[:2], id=param.id) for param in SENSES])
    async def test_call_57_62_a_switch_after_the_agent_left_is_refused_and_records_nothing(
        self, participation, member_hearing, call, signal, sense
    ):
        """CALL-57 and CALL-62: A switch frame for an agent that has left is refused and records nothing."""
        host, guest, agent, _host_agent = call
        await participation.leave(GUEST, room_of(host))

        await guest.say(switch(signal, agent.agent_id, on=True))

        assert (refusals(guest), subjects_with(member_hearing, host, guest, sense)) == (
            [AgentRefusal.GONE.value],
            set(),
        )


class TestAFrameWithoutItsFields:
    @pytest.mark.parametrize(
        ("signal", "data"),
        [
            pytest.param(signal, data, id=f"{name}-{index}")
            for name, signal, forms in [
                ("hearing", HEARING, [{}, {"agent_id": 7, "on": True}, {"agent_id": "x", "on": "yes"}]),
                ("seeing", SEEING, [{}, {"agent_id": 7, "on": True}, {"agent_id": "x", "on": "yes"}]),
                (
                    "look_part",
                    ParticipationSignal.AGENT_LOOK_PART,
                    [
                        {},
                        {"request_id": 7},
                        {"request_id": "x", "index": "0", "count": 1, "data": "AAAA"},
                        {"request_id": "x", "index": 0, "count": 1},
                    ],
                ),
                ("look_none", ParticipationSignal.AGENT_LOOK_NONE, [{}]),
            ]
            for index, data in enumerate(forms)
        ],
    )
    async def test_call_57_62_63_a_frame_that_names_no_agent_or_request_in_its_form_is_an_invalid_message(
        self, call, signal, data
    ):
        """CALL-57, CALL-62 and CALL-63: A switch names the agent by its id and says on or off as a boolean; a look's
        answer names its request, and a part its index, the count of parts and its data."""
        _host, guest, _agent, _host_agent = call

        await guest.say({"type": signal.value, **data})

        assert errors(guest) == [ErrorReason.INVALID_MESSAGE.value]
