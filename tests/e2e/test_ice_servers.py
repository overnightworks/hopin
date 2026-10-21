import json
from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import Page, Route, expect

from tests.e2e.conftest import (
    DESKTOP,
    ICE_SERVERS_PATH,
    ICE_SERVERS_URL_PATTERN,
    MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    RECORD_PEER_CONNECTIONS,
    join_by_link,
    press_start_call,
    start_call,
)

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.e2e

RECORD_MEDIA_STREAMS = """
window.acquiredStreams = [];
const nativeGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
navigator.mediaDevices.getUserMedia = async (constraints) => {
    const stream = await nativeGetUserMedia(constraints);
    window.acquiredStreams.push(stream);
    return stream;
};
"""
RECORDED_CONFIGURATIONS_AFTER_FIRST = """
async (timeoutMilliseconds) => {
    const deadline = performance.now() + timeoutMilliseconds;
    while (window.recordedConfigurations.length === 0 && performance.now() < deadline) {
        await new Promise((resume) => requestAnimationFrame(resume));
    }
    return window.recordedConfigurations;
}
"""
LIVE_TRACK_COUNT = """
() => window.acquiredStreams.flatMap((stream) => stream.getTracks())
    .filter((track) => track.readyState === "live").length
"""
ACQUIRED_TRACK_COUNT = "() => window.acquiredStreams.flatMap((stream) => stream.getTracks()).length"


def test_call_peer_connections_use_the_servers_ice_servers(open_person: Callable[..., Page], base_url: str) -> None:
    host = open_person(**DESKTOP)
    guest = open_person(**DESKTOP)
    for person in (host, guest):
        person.add_init_script(RECORD_PEER_CONNECTIONS)
    served = host.request.get(f"{base_url}{ICE_SERVERS_PATH}").json()["iceServers"]

    invite_link = start_call(host, base_url)
    join_by_link(guest, invite_link)

    for person in (host, guest):
        configurations = person.evaluate(RECORDED_CONFIGURATIONS_AFTER_FIRST, MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
        assert configurations
        assert all(configuration["iceServers"] == served for configuration in configurations)


def test_call_start_with_failing_ice_servers_shows_the_error_and_releases_the_camera(
    open_person: Callable[..., Page], base_url: str
) -> None:
    host = open_person(**DESKTOP)
    host.add_init_script(RECORD_MEDIA_STREAMS)

    def refuse(route: Route) -> None:
        route.fulfill(status=500, content_type="application/json", body=json.dumps({}))

    host.route(ICE_SERVERS_URL_PATTERN, refuse)
    press_start_call(host, base_url)

    expect(host.locator("#status")).to_contain_text("Could not load ICE servers")
    assert host.evaluate(ACQUIRED_TRACK_COUNT) > 0
    assert host.evaluate(LIVE_TRACK_COUNT) == 0
