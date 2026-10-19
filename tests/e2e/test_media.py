from collections.abc import Callable

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import (
    DESKTOP,
    RECORD_PEER_CONNECTIONS,
    SHARE_THE_SCREEN_FROM_A_CANVAS,
    join,
    share_then_stop_the_screen_share,
    start_call,
    wait_for_peer_count,
)

pytestmark = pytest.mark.e2e

CAMERA_TRACK_AND_ITS_SENDER_TRACK = """
() => {
    const [localTrack] = document.getElementById("localVideo").srcObject.getVideoTracks();
    const sent = window.recordedConnections
        .flatMap((connection) => connection.getSenders())
        .map((sender) => sender.track)
        .find((track) => track && track.id === localTrack.id);
    return sent ? { local: localTrack.enabled, sent: sent.enabled } : null;
}
"""


@pytest.mark.parametrize(
    ("camera_turned_off", "button_text"),
    [(True, "Cam Off"), (False, "Cam On")],
    ids=["off", "on"],
)
def test_stop_sharing_restores_the_camera_as_the_person_left_it(
    open_person: Callable[..., Page], base_url: str, camera_turned_off: bool, button_text: str
) -> None:
    host = open_person(**DESKTOP)
    host.add_init_script(RECORD_PEER_CONNECTIONS)
    host.add_init_script(SHARE_THE_SCREEN_FROM_A_CANVAS)
    join(open_person, start_call(host, base_url))
    wait_for_peer_count(host, 1)
    if camera_turned_off:
        host.click("#videoBtn")

    share_then_stop_the_screen_share(host)

    expect(host.locator("#videoBtn")).to_have_text(button_text)
    host.wait_for_function(CAMERA_TRACK_AND_ITS_SENDER_TRACK)
    expected = not camera_turned_off
    assert host.evaluate(CAMERA_TRACK_AND_ITS_SENDER_TRACK) == {"local": expected, "sent": expected}
