"""CALL-76: a seat reports how its peer connections went as a closed code, which the server writes down."""

import pytest
import pytest_asyncio
import structlog.testing

from hopin.config.constants import CONNECTION_OUTCOME_RATE, ConnectionOutcome, ErrorReason, SignalType
from hopin.rooms.reference import room_reference
from tests.signaling.support.browsers import Link, host_and_guest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

OUTCOME_EVENT = "connection.outcome"


@pytest_asyncio.fixture
async def link(handler):
    link = Link(handler)
    yield link
    await link.close_all()


def report(outcome):
    return {"type": SignalType.CONNECTION_OUTCOME.value, "outcome": outcome}


def outcomes_in(logs):
    return [entry for entry in logs if entry["event"] == OUTCOME_EVENT]


@pytest.mark.parametrize("outcome", list(ConnectionOutcome))
async def test_call_76_each_outcome_is_logged_once_with_the_room_and_its_code_only(link, outcome):
    host, guest = await host_and_guest(link)

    with structlog.testing.capture_logs() as logs:
        await guest.say(report(outcome.value))

    assert outcomes_in(logs) == [
        {
            "event": OUTCOME_EVENT,
            "room_ref": room_reference(host.room_id),
            "outcome": outcome.value,
            "log_level": "info",
        }
    ]


async def test_call_76_a_code_outside_the_set_is_refused_and_not_logged(link):
    _, guest = await host_and_guest(link)

    with structlog.testing.capture_logs() as logs:
        await guest.say(report("teleported"))

    assert outcomes_in(logs) == []
    assert guest.frames(SignalType.ERROR)[-1]["reason"] == ErrorReason.INVALID_MESSAGE.value


async def test_call_76_a_report_is_answered_with_no_frame(link):
    _, guest = await host_and_guest(link)
    sent_before = len(guest.socket.sent)

    await guest.say(report(ConnectionOutcome.DIRECT.value))

    assert len(guest.socket.sent) == sent_before


async def test_call_76_a_seat_is_written_down_at_most_the_rate_in_one_window_and_another_seat_is_not_held_back(link):
    host, guest = await host_and_guest(link)

    with structlog.testing.capture_logs() as logs:
        for _ in range(CONNECTION_OUTCOME_RATE.most + 3):
            await guest.say(report(ConnectionOutcome.DIRECT.value))
        await host.say(report(ConnectionOutcome.RELAYED.value))

    codes = [entry["outcome"] for entry in outcomes_in(logs)]
    assert codes.count(ConnectionOutcome.DIRECT.value) == CONNECTION_OUTCOME_RATE.most
    assert codes.count(ConnectionOutcome.RELAYED.value) == 1
