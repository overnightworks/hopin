"""An agent with an account of its own, an owner and the adders group, as the feature-level tests arrange them."""

import asyncio

from hopin.participation.feature import GROUPS_READ_TASK_NAME, OwnAccount
from hopin.participation.vocabulary import ParticipationSignal
from tests.participation.support.people import person_named
from tests.signaling.support.browsers import SETTLE_TIMEOUT_SECONDS

OWNER = "owner"
ADDERS = "/apps/hopin/agent-adders"
AGENT = person_named("agent-subject")
NAME = "Claude"
WALL_CLOCK_SECONDS = 1000.0


async def agent_calls(participation, *, owner=OWNER, name=NAME):
    await participation.agent_called(AGENT, OwnAccount(owner, name))


def _running_reads():
    return [task for task in asyncio.all_tasks() if task.get_name() == GROUPS_READ_TASK_NAME]


async def let_reads_finish():
    """Wait until every read of a seat's groups that is running has ended, with its announcement."""
    async with asyncio.timeout(SETTLE_TIMEOUT_SECONDS):
        while reads := _running_reads():
            await asyncio.gather(*reads)


class GroupReadLog(list):
    """The names of the seats whose groups were read, in order; `asked` is set at each new entry."""

    def __init__(self):
        super().__init__()
        self.asked = asyncio.Event()

    def append(self, name):
        super().append(name)
        self.asked.set()


async def let_reads_start(group_reads, count):
    """Wait until `count` reads have asked for a seat's groups, for a read the test holds back."""
    async with asyncio.timeout(SETTLE_TIMEOUT_SECONDS):
        while len(group_reads) < count:
            group_reads.asked.clear()
            await group_reads.asked.wait()


def offers_sent_to(browser):
    """The offers of the last `agent_offers` frame the browser was sent, or nothing when it got none."""
    frames = browser.frames(ParticipationSignal.AGENT_OFFERS)
    return frames[-1]["offers"] if frames else None


def control_shown_to(browser):
    """Whether the last `agent_offers` frame the browser was sent shows the control, or nothing when it got none."""
    frames = browser.frames(ParticipationSignal.AGENT_OFFERS)
    return frames[-1]["shown"] if frames else None
