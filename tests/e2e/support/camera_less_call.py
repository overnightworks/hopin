from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tests.e2e.support.calls import join, record_page_errors, start_call
from tests.e2e.support.media import (
    COUNT_OFFERS,
    MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    RECORD_MEDIA_TRACKS,
    RECORD_PEER_CONNECTIONS,
    REFUSE_THE_CAMERA,
    wait_for_peer_count,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from playwright.sync_api import Page

CAMERA_OFF_AT_JOIN = (
    REFUSE_THE_CAMERA,
    RECORD_MEDIA_TRACKS,
    RECORD_PEER_CONNECTIONS,
    COUNT_OFFERS,
)


@dataclass
class CameraLessCall:
    host: Page
    guest: Page
    invite_link: str


@dataclass
class CameraLessHostCall:
    host: Page
    later_joiner: Page
    invite_link: str


def start_camera_less_call(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any], page_errors: list[str]
) -> CameraLessCall:
    host = record_page_errors(open_person(**form_factor), page_errors)
    host.add_init_script(RECORD_PEER_CONNECTIONS)
    host.add_init_script(COUNT_OFFERS)
    invite_link = start_call(host, base_url)
    guest = join(open_person, invite_link, form_factor, CAMERA_OFF_AT_JOIN, page_errors)
    for person in (host, guest):
        wait_for_peer_count(person, 1)
    return CameraLessCall(host, guest, invite_link)


def start_camera_less_host_call(
    open_person: Callable[..., Page],
    base_url: str,
    form_factor: dict[str, Any],
    page_errors: list[str],
    host_init_scripts: tuple[str, ...] = (),
) -> CameraLessHostCall:
    host = record_page_errors(open_person(**form_factor), page_errors)
    for script in (RECORD_PEER_CONNECTIONS, COUNT_OFFERS, *host_init_scripts):
        host.add_init_script(script)
    invite_link = start_call(host, base_url, with_camera=False)
    later_joiner = join(open_person, invite_link, form_factor, (RECORD_PEER_CONNECTIONS, COUNT_OFFERS), page_errors)
    for person in (host, later_joiner):
        wait_for_peer_count(person, 1)
    return CameraLessHostCall(host, later_joiner, invite_link)


CHANGE_THE_VIDEO_DIRECTION = """
(direction) => {
    window.recordedConnections[0].getTransceivers()
        .filter((transceiver) => transceiver.receiver.track.kind === "video")
        .forEach((transceiver) => { transceiver.direction = direction; });
}
"""
VIDEO_DIRECTION_IS_AGREED = """
(direction) => window.recordedConnections[0].getTransceivers()
    .filter((transceiver) => transceiver.receiver.track.kind === "video")
    .every((transceiver) => transceiver.currentDirection === direction)
"""


def renegotiate_the_video_direction(person: Page, direction: str) -> None:
    """The change makes the page offer by itself; the other side answers through the call's signaling."""
    person.evaluate(CHANGE_THE_VIDEO_DIRECTION, direction)
    person.wait_for_function(VIDEO_DIRECTION_IS_AGREED, arg=direction, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
