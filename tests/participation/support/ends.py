"""The ways a dialed-in agent's presence ends, each a step on `participation` with the host's and the guest's browsers
and the agent, where the guest is the agent's sponsor."""

from hopin.config.constants import SignalType
from hopin.participation.vocabulary import ParticipationSignal
from tests.participation.support.people import person_named, room_of

GUEST = person_named("guest")


async def leave_by_the_agent(participation, host, guest, agent):
    await participation.leave(GUEST, room_of(host))


async def remove_by_the_host(participation, host, guest, agent):
    await host.say({"type": ParticipationSignal.AGENT_REMOVE.value, "agent_id": agent.agent_id})


async def leave_by_the_sponsor(participation, host, guest, agent):
    await guest.drop(1000)


async def end_by_the_host(participation, host, guest, agent):
    await host.say({"type": SignalType.END_ROOM.value})


async def shut_down(participation, host, guest, agent):
    await participation.shut_down()
