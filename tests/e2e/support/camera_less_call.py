from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from tests.e2e.support.calls import join, record_page_errors, start_call
from tests.e2e.support.media import (
    COUNT_OFFERS,
    RECORD_MEDIA_TRACKS,
    RECORD_PEER_CONNECTIONS,
    REFUSE_THE_CAMERA,
    wait_for_peer_count,
)
from tests.e2e.support.people import DESKTOP

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


@pytest.fixture
def form_factor() -> dict[str, Any]:
    return DESKTOP


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


@pytest.fixture
def camera_less_call(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any], page_errors: list[str]
) -> CameraLessCall:
    return start_camera_less_call(open_person, base_url, form_factor, page_errors)


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


@pytest.fixture
def camera_less_host_call(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any], page_errors: list[str]
) -> CameraLessHostCall:
    return start_camera_less_host_call(open_person, base_url, form_factor, page_errors)


OFFER_THE_VIDEO_DIRECTION = """
async (direction) => {
    const connection = window.recordedConnections[0];
    connection.getTransceivers()
        .filter((transceiver) => transceiver.receiver.track.kind === "video")
        .forEach((transceiver) => { transceiver.direction = direction; });
    await connection.setLocalDescription(await connection.createOffer());
    return connection.localDescription.sdp;
}
"""
ANSWER_THE_OFFER = """
async (sdp) => {
    const connection = window.recordedConnections[0];
    await connection.setRemoteDescription({ type: "offer", sdp });
    await connection.setLocalDescription(await connection.createAnswer());
    return connection.localDescription.sdp;
}
"""
ACCEPT_THE_ANSWER = "(sdp) => window.recordedConnections[0].setRemoteDescription({ type: 'answer', sdp })"


def renegotiate_the_video_direction(offerer: Page, answerer: Page, direction: str) -> None:
    offer = offerer.evaluate(OFFER_THE_VIDEO_DIRECTION, direction)
    offerer.evaluate(ACCEPT_THE_ANSWER, answerer.evaluate(ANSWER_THE_OFFER, offer))
