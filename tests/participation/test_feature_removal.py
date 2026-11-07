import pytest

from hopin.config.constants import ErrorReason, SignalType
from hopin.participation.vocabulary import AgentRefusal, ParticipationSignal
from tests.participation.support.people import person_named, room_of

pytestmark = pytest.mark.integration


def frames_of(browser, signal):
    return [frame for socket in browser.sockets for frame in socket.sent if frame["type"] == signal.value]


async def three_in_a_call_with_an_agent_of_the_guest(link, participation):
    host = await link.join("host")
    guest = await link.join("guest", room_of(host))
    bystander = await link.join("bystander", room_of(host))
    agent = await participation.dial_in(person_named("guest"), room_of(host), "Claude")
    return host, guest, bystander, agent


async def remove(browser, agent):
    await browser.say({"type": ParticipationSignal.AGENT_REMOVE.value, "agent_id": agent.agent_id})


@pytest.mark.asyncio
class TestRemovalFromTheTile:
    @pytest.mark.parametrize("remover", ["guest", "host"])
    async def test_call_49_the_sponsor_or_the_host_removes_the_agent_and_every_seat_is_told(
        self, link, participation, remover
    ):
        """CALL-49: The agent's sponsor or the call's host removes an agent from its tile, and every seat is told."""
        host, guest, bystander, agent = await three_in_a_call_with_an_agent_of_the_guest(link, participation)

        await remove({"host": host, "guest": guest}[remover], agent)

        assert [len(frames_of(browser, ParticipationSignal.AGENT_LEFT)) for browser in (host, guest, bystander)] == [
            1,
            1,
            1,
        ]
        assert await participation.leave(person_named("guest"), agent.agent_id) is AgentRefusal.GONE

    async def test_call_49_anyone_else_is_refused_and_the_agent_stays(self, link, participation):
        """CALL-49: A removal by anyone but the sponsor or the host is refused to the one who asked."""
        host, guest, bystander, agent = await three_in_a_call_with_an_agent_of_the_guest(link, participation)

        await remove(bystander, agent)

        [refusal] = frames_of(bystander, ParticipationSignal.AGENT_REFUSED)
        assert refusal["reason"] == AgentRefusal.NOT_SPONSOR_OR_HOST.value
        assert [frames_of(browser, ParticipationSignal.AGENT_LEFT) for browser in (host, guest, bystander)] == [[]] * 3
        assert await participation.leave(person_named("guest"), agent.agent_id) is None

    async def test_call_49_a_seat_of_another_call_removes_nothing(self, link, participation):
        """CALL-49: A removal from a seat outside the agent's call is ignored."""
        host, _guest, _bystander, agent = await three_in_a_call_with_an_agent_of_the_guest(link, participation)
        outsider = await link.join("outsider")

        await remove(outsider, agent)

        assert frames_of(outsider, ParticipationSignal.AGENT_REFUSED) == []
        assert frames_of(host, ParticipationSignal.AGENT_LEFT) == []

    async def test_a_removal_of_an_agent_already_gone_is_quiet(self, link, participation):
        host, guest, _bystander, agent = await three_in_a_call_with_an_agent_of_the_guest(link, participation)
        await remove(guest, agent)

        await remove(host, agent)

        assert len(frames_of(host, ParticipationSignal.AGENT_LEFT)) == 1

    @pytest.mark.parametrize("data", [{}, {"agent_id": 7}])
    async def test_a_removal_without_an_agent_id_is_an_invalid_message(self, link, participation, data):
        host = await link.join("host")

        await host.say({"type": ParticipationSignal.AGENT_REMOVE.value, **data})

        assert [frame["reason"] for frame in host.frames(SignalType.ERROR)] == [ErrorReason.INVALID_MESSAGE.value]
