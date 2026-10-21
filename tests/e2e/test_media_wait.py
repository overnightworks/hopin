from collections.abc import Callable

import pytest
from playwright.sync_api import Page

from tests.e2e import conftest
from tests.e2e.conftest import (
    DESKTOP,
    RECORD_PEER_CONNECTIONS,
    inbound_per_connection,
    join,
    start_call,
    wait_for_inbound_growth,
    wait_for_peer_count,
)

pytestmark = pytest.mark.e2e

SHORT_WAIT_MILLISECONDS = 1_500


def test_wait_for_inbound_growth_times_out_naming_the_last_values_when_nothing_is_sent(
    open_person: Callable[..., Page], monkeypatch: pytest.MonkeyPatch
) -> None:
    person = open_person(**DESKTOP)
    person.add_init_script(RECORD_PEER_CONNECTIONS)
    person.goto("about:blank")
    person.evaluate("window.recordedConnections.push(new RTCPeerConnection())")
    baseline = inbound_per_connection(person, "audio", "bytesReceived")
    monkeypatch.setattr(conftest, "MEDIA_FLOWING_TIMEOUT_MILLISECONDS", SHORT_WAIT_MILLISECONDS)

    with pytest.raises(AssertionError, match=r"last measured \[0"):
        wait_for_inbound_growth(person, "audio", "bytesReceived", baseline)


def test_wait_for_inbound_growth_returns_when_the_peer_sends(open_person: Callable[..., Page], base_url: str) -> None:
    host = open_person(**DESKTOP)
    host.add_init_script(RECORD_PEER_CONNECTIONS)
    invite_link = start_call(host, base_url)
    guest = join(open_person, invite_link, DESKTOP, (RECORD_PEER_CONNECTIONS,))
    wait_for_peer_count(guest, 1)

    wait_for_inbound_growth(guest, "audio", "bytesReceived", [0.0])
