import pytest
from agents.mcp_server.hearing import MemberHearing

from hopin.agent.hearing import MemberHearingRecord
from hopin.participation.feature import Participation
from hopin.participation.hearing import Hearing
from hopin.participation.participants import OwnAccount, Person
from hopin.participation.voice import VoiceStore
from hopin.signaling.handler import SignalingHandler
from hopin.signaling.scheduler import InMemoryDeadlines
from tests.support.signaling_fakes import ManualScheduler, SocketAccountResolver


async def groups_never_read(connection):
    raise AssertionError("nobody adds, so no groups are read")


pytestmark = pytest.mark.asyncio

LEASE = 30.0
PERSON = Person("issuer", "agent-subject")
OWNER = OwnAccount("owner", "Claude")
OTHER_OWNER = OwnAccount("other owner", "Claude")


@pytest.fixture
def scheduler():
    return ManualScheduler()


@pytest.fixture
def deadlines(scheduler):
    return InMemoryDeadlines(scheduler)


@pytest.fixture
def changed():
    return []


@pytest.fixture
def handler(call_store, scheduler, deadlines, changed):
    """A call's handler running participation, which no seat ever reaches."""
    return SignalingHandler(
        call_store, SocketAccountResolver(), scheduler, deadlines, [_participation(deadlines, changed)]
    )


@pytest.fixture
def participation(handler):
    [host] = handler.features
    return host.feature


def _participation(deadlines, changed):
    return Participation(
        deadlines=deadlines,
        lease_seconds=LEASE,
        on_changed=changed.append,
        on_window_changed=lambda person, room_id: None,
        wall_clock=lambda: 0.0,
        groups_of=groups_never_read,
        listens_to_standing=lambda person: False,
        figure_sources=(),
        hearing=Hearing(MemberHearingRecord(MemberHearing()), enabled=False),
        voices=VoiceStore(),
    )


class TestTheLeaseTimer:
    async def test_a_lease_nobody_renews_ends_once_when_its_timer_runs(self, participation, scheduler, changed):
        await participation.agent_called(PERSON, OWNER)

        await scheduler.advance(LEASE)

        assert changed == [PERSON]

    async def test_a_renewed_lease_ends_at_its_new_deadline(self, participation, scheduler, changed):
        await participation.agent_called(PERSON, OWNER)
        await scheduler.advance(LEASE - 1)
        await participation.agent_called(PERSON, OWNER)

        await scheduler.advance(LEASE - 1)
        assert changed == []

        await scheduler.advance(1)
        assert changed == [PERSON]

    async def test_a_late_call_ends_the_old_lease_once_and_the_stale_timer_adds_no_change(
        self, participation, scheduler, changed
    ):
        await participation.agent_called(PERSON, OWNER)
        scheduler.skip(LEASE)
        await participation.agent_called(PERSON, OWNER)
        assert changed == [PERSON]

        await scheduler.advance(1)

        assert changed == [PERSON]

    async def test_a_changed_owner_ends_the_old_lease_once_and_the_first_timer_adds_no_change(
        self, participation, scheduler, changed
    ):
        await participation.agent_called(PERSON, OWNER)
        await scheduler.advance(LEASE / 2)
        await participation.agent_called(PERSON, OTHER_OWNER)
        assert changed == [PERSON]

        await scheduler.advance(LEASE / 2)

        assert changed == [PERSON]

    async def test_a_lease_timer_that_outlives_the_shut_down_adds_no_change(
        self, handler, participation, scheduler, changed
    ):
        await participation.agent_called(PERSON, OWNER)
        await handler.shut_down()

        await scheduler.advance(LEASE)

        assert changed == []
