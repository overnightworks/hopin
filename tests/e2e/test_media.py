from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import (
    COUNT_OFFERS,
    DESKTOP,
    EVERY_VIDEO_CAP_IS,
    FORM_FACTORS,
    MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    OFFER_COUNT,
    OTHER_CAMERA_OFF,
    OTHER_VIDEO,
    PLUG_IN_THE_CAMERA,
    RECORD_PEER_CONNECTIONS,
    REFUSE_THE_CAMERA,
    SHARE_THE_SCREEN_FROM_A_CANVAS,
    expect_the_tile_to_play_audio,
    inbound_per_connection,
    join,
    share_then_stop_the_screen_share,
    start_call,
    wait_for_frames_to_advance,
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


CAMERA_OFF_AT_JOIN = (REFUSE_THE_CAMERA, RECORD_PEER_CONNECTIONS, COUNT_OFFERS, SHARE_THE_SCREEN_FROM_A_CANVAS)
STREAM_IDS_ON_THE_WIRE = r"""
() => {
    const [, ...sections] = window.recordedConnections[0].localDescription.sdp.split("\nm=");
    const streamIdOf = (kind) => sections.find((section) => section.startsWith(kind))?.match(/a=msid:(\S+)/)?.[1];
    return {
        local: document.getElementById("localVideo").srcObject.id,
        audio: streamIdOf("audio"),
        video: streamIdOf("video"),
    };
}
"""
TILE_TRACKS = """
(selector) => document.querySelector(selector).srcObject.getTracks().map((track) => `${track.kind}:${track.readyState}`)
"""
PICTURE_ONLY_ON_THE_TILES = f"{OTHER_VIDEO}:not(.hidden)"
ICON_ONLY_ON_THE_TILES = f"{OTHER_CAMERA_OFF}:not(.hidden)"


@dataclass
class CameraLessCall:
    host: Page
    guest: Page
    invite_link: str


@pytest.fixture
def form_factor() -> dict[str, Any]:
    return DESKTOP


@pytest.fixture
def page_errors() -> Iterator[list[str]]:
    errors: list[str] = []
    yield errors
    assert errors == []


def _record_page_errors(person: Page, page_errors: list[str]) -> Page:
    person.on("pageerror", lambda error: page_errors.append(str(error)))
    return person


@pytest.fixture
def camera_less_call(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any], page_errors: list[str]
) -> CameraLessCall:
    host = _record_page_errors(open_person(**form_factor), page_errors)
    host.add_init_script(RECORD_PEER_CONNECTIONS)
    host.add_init_script(COUNT_OFFERS)
    invite_link = start_call(host, base_url)
    guest = join(open_person, invite_link, form_factor, CAMERA_OFF_AT_JOIN)
    _record_page_errors(guest, page_errors)
    for person in (host, guest):
        wait_for_peer_count(person, 1)
    return CameraLessCall(host, guest, invite_link)


def _turn_the_camera_on(person: Page) -> None:
    person.evaluate(PLUG_IN_THE_CAMERA)
    expect(person.locator("#videoBtn")).to_have_text("Cam Off")
    person.click("#videoBtn")
    expect(person.locator("#videoBtn")).to_have_text("Cam On")


def _offers_made(call: CameraLessCall) -> list[int]:
    return [call.host.evaluate(OFFER_COUNT), call.guest.evaluate(OFFER_COUNT)]


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_person_without_a_camera_sees_and_hears_everyone_else(camera_less_call: CameraLessCall) -> None:
    guest = camera_less_call.guest

    expect(guest.locator(OTHER_VIDEO)).to_be_visible()
    expect(guest.locator(OTHER_CAMERA_OFF)).to_be_hidden()
    wait_for_frames_to_advance(guest)
    expect_the_tile_to_play_audio(guest, OTHER_VIDEO)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_person_without_a_camera_shows_the_icon_and_sends_no_picture_but_is_heard(
    camera_less_call: CameraLessCall,
) -> None:
    host = camera_less_call.host

    expect(host.locator(OTHER_CAMERA_OFF)).to_be_visible()
    expect_the_tile_to_play_audio(host, OTHER_VIDEO)
    assert inbound_per_connection(host, "video", "framesDecoded") == [0]


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_camera_turned_on_mid_call_reaches_the_people_who_were_there_under_the_cap_without_an_offer(
    camera_less_call: CameraLessCall,
) -> None:
    host, guest = camera_less_call.host, camera_less_call.guest
    offers_before = _offers_made(camera_less_call)

    _turn_the_camera_on(guest)

    expect(host.locator(OTHER_CAMERA_OFF)).to_be_hidden()
    wait_for_frames_to_advance(host)
    expect_the_tile_to_play_audio(host, OTHER_VIDEO)
    expect_the_tile_to_play_audio(guest, OTHER_VIDEO)
    assert _offers_made(camera_less_call) == offers_before
    guest.wait_for_function(EVERY_VIDEO_CAP_IS, arg=[1, 2_000_000], timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_picture_arrives_under_the_stream_of_the_voice(camera_less_call: CameraLessCall) -> None:
    host, guest = camera_less_call.host, camera_less_call.guest

    _turn_the_camera_on(guest)

    expect(host.locator(OTHER_CAMERA_OFF)).to_be_hidden()
    assert sorted(host.evaluate(TILE_TRACKS, OTHER_VIDEO)) == ["audio:live", "video:live"]
    stream_ids = guest.evaluate(STREAM_IDS_ON_THE_WIRE)
    assert stream_ids["audio"] == stream_ids["video"] == stream_ids["local"]


def test_a_camera_turned_on_before_a_late_joiner_arrives_reaches_the_late_joiner_under_the_cap(
    camera_less_call: CameraLessCall, open_person: Callable[..., Page], page_errors: list[str]
) -> None:
    guest = camera_less_call.guest
    _turn_the_camera_on(guest)
    wait_for_frames_to_advance(camera_less_call.host)

    late_joiner = join(open_person, camera_less_call.invite_link, init_scripts=(RECORD_PEER_CONNECTIONS,))
    _record_page_errors(late_joiner, page_errors)

    wait_for_peer_count(late_joiner, 2)
    expect(late_joiner.locator(PICTURE_ONLY_ON_THE_TILES)).to_have_count(2)
    expect(late_joiner.locator(ICON_ONLY_ON_THE_TILES)).to_have_count(0)
    wait_for_frames_to_advance(late_joiner)
    guest.wait_for_function(EVERY_VIDEO_CAP_IS, arg=[2, 1_000_000], timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


def test_a_camera_turned_off_and_on_again_shows_the_icon_then_the_picture_without_an_offer(
    camera_less_call: CameraLessCall,
) -> None:
    host, guest = camera_less_call.host, camera_less_call.guest
    _turn_the_camera_on(guest)
    wait_for_frames_to_advance(host)
    offers_before = _offers_made(camera_less_call)

    guest.click("#videoBtn")

    expect(host.locator(OTHER_CAMERA_OFF)).to_be_visible()
    assert guest.evaluate(CAMERA_TRACK_AND_ITS_SENDER_TRACK) == {"local": False, "sent": False}
    guest.click("#videoBtn")
    expect(host.locator(OTHER_CAMERA_OFF)).to_be_hidden()
    wait_for_frames_to_advance(host)
    assert _offers_made(camera_less_call) == offers_before


@pytest.mark.parametrize(
    ("camera_turned_off", "sent_state"),
    [(False, {"local": True, "sent": True}), (True, {"local": False, "sent": False})],
    ids=["on", "off"],
)
def test_stop_sharing_keeps_a_camera_added_mid_call_as_the_person_left_it(
    camera_less_call: CameraLessCall, camera_turned_off: bool, sent_state: dict[str, bool]
) -> None:
    host, guest = camera_less_call.host, camera_less_call.guest
    _turn_the_camera_on(guest)
    wait_for_frames_to_advance(host)
    if camera_turned_off:
        guest.click("#videoBtn")

    share_then_stop_the_screen_share(guest)

    guest.wait_for_function(CAMERA_TRACK_AND_ITS_SENDER_TRACK)
    assert guest.evaluate(CAMERA_TRACK_AND_ITS_SENDER_TRACK) == sent_state
    if camera_turned_off:
        expect(host.locator(OTHER_CAMERA_OFF)).to_be_visible()
    else:
        wait_for_frames_to_advance(host)
