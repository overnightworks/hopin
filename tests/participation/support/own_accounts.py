"""An agent with an account of its own, an owner and the adders group, as the feature-level tests arrange them."""

import asyncio

from hopin.participation.feature import OwnAccount
from hopin.participation.vocabulary import ParticipationSignal
from tests.participation.support.people import person_named

OWNER = "owner"
ADDERS = "/apps/hopin/agent-adders"
AGENT = person_named("agent-subject")
NAME = "Claude"
SETTLE_TURNS = 20


async def agent_calls(participation, *, owner=OWNER, name=NAME):
    await participation.agent_called(AGENT, OwnAccount(owner, name))


async def let_reads_finish():
    """Give the tasks that read a seat's groups the turns they need; they wait on no clock."""
    for _ in range(SETTLE_TURNS):
        await asyncio.sleep(0)


def offers_sent_to(browser):
    """The offers of the last `agent_offers` frame the browser was sent, or nothing when it got none."""
    frames = browser.frames(ParticipationSignal.AGENT_OFFERS)
    return frames[-1]["offers"] if frames else None
