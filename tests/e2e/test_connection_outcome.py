from collections import Counter
from typing import TYPE_CHECKING

import pytest
import structlog.testing

from hopin.config.constants import ConnectionOutcome
from tests.e2e.support.mesh_pair import FIRST_CONNECTION_IS_CONNECTED, FORCE_STATE, ConnectedPair, pump_until, wait_for

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.e2e

OUTCOME_EVENT = "connection.reported"


@pytest.fixture
def server_logs() -> Iterator[list[dict[str, str]]]:
    """The server's log events from before the call is set up until the test ends."""
    with structlog.testing.capture_logs() as logs:
        yield logs


def _outcomes(logs: list[dict[str, str]]) -> Counter[str]:
    return Counter(entry["outcome"] for entry in list(logs) if entry["event"] == OUTCOME_EVENT)


def test_a_dropped_connection_that_comes_back_is_logged_by_the_server_as_failed_then_resumed(
    server_logs: list[dict[str, str]], connected_pair: ConnectedPair
) -> None:
    """CALL-76: each seat's connection is logged as direct, and a forced drop that the ICE restart heals as failed
    and then resumed."""
    guest = connected_pair.guest
    direct = ConnectionOutcome.DIRECT.value
    pump_until(guest, lambda: _outcomes(server_logs) == Counter({direct: 2}))

    connected_pair.force_failed_on(guest)
    pump_until(guest, lambda: _outcomes(server_logs)[ConnectionOutcome.FAILED.value] == 1)
    wait_for(guest, FIRST_CONNECTION_IS_CONNECTED)
    guest.evaluate(FORCE_STATE, None)

    pump_until(guest, lambda: _outcomes(server_logs)[ConnectionOutcome.RESUMED.value] == 1)
    assert _outcomes(server_logs) == Counter({direct: 2, "failed": 1, "resumed": 1})
