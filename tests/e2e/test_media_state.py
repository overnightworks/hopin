from collections.abc import Callable
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import (
    DESKTOP,
    MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    PHONE,
    REFUSE_THE_CAMERA,
    join,
    start_call,
)

pytestmark = pytest.mark.e2e

FORM_FACTORS = [pytest.param(DESKTOP, id="desktop"), pytest.param(PHONE, id="phone")]
OTHER_TILE = "#videoGrid .tile:not(.self)"
OTHER_MUTE_MARK = f"{OTHER_TILE} .name .muted"
OTHER_VIDEO = f"{OTHER_TILE} video"
OTHER_CAMERA_OFF = f"{OTHER_TILE} .vid.off"
OWN_MUTE_MARK = "#localContainer .name .muted"
OTHER_VIDEO_HAS_A_PICTURE = """
(selector) => document.querySelector(selector).videoWidth > 0
"""


def host_and_guest(open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any]) -> tuple[Page, Page]:
    host = open_person(**form_factor)
    guest = join(open_person, start_call(host, base_url), form_factor)
    expect(host.locator("#stage")).to_have_attribute("data-count", "2")
    expect(guest.locator("#stage")).to_have_attribute("data-count", "2")
    return host, guest


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_media_state_shows_the_mute_mark_on_the_tile_of_the_person_who_muted(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any]
) -> None:
    host, guest = host_and_guest(open_person, base_url, form_factor)
    expect(host.locator(OTHER_MUTE_MARK)).to_be_hidden()

    guest.click("#muteBtn")
    expect(host.locator(OTHER_MUTE_MARK)).to_be_visible()

    guest.click("#muteBtn")
    expect(host.locator(OTHER_MUTE_MARK)).to_be_hidden()


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_media_state_shows_the_own_mute_mark_on_the_own_tile(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any]
) -> None:
    host = open_person(**form_factor)
    start_call(host, base_url)
    expect(host.locator(OWN_MUTE_MARK)).to_be_hidden()

    host.click("#muteBtn")
    expect(host.locator(OWN_MUTE_MARK)).to_be_visible()

    host.click("#muteBtn")
    expect(host.locator(OWN_MUTE_MARK)).to_be_hidden()


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_media_state_shows_the_person_icon_while_the_camera_is_off_and_the_video_when_it_is_on_again(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any]
) -> None:
    host, guest = host_and_guest(open_person, base_url, form_factor)
    host.wait_for_function(OTHER_VIDEO_HAS_A_PICTURE, arg=OTHER_VIDEO, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

    guest.click("#videoBtn")
    expect(host.locator(OTHER_CAMERA_OFF)).to_be_visible()
    expect(host.locator(f"{OTHER_CAMERA_OFF} .av svg")).to_be_visible()
    expect(host.locator(OTHER_VIDEO)).to_be_hidden()

    guest.click("#videoBtn")
    expect(host.locator(OTHER_CAMERA_OFF)).to_be_hidden()
    expect(host.locator(OTHER_VIDEO)).to_be_visible()


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_media_state_reaches_a_person_who_joins_after_the_host_muted(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any]
) -> None:
    host = open_person(**form_factor)
    invite_link = start_call(host, base_url)
    host.click("#muteBtn")

    guest = join(open_person, invite_link, form_factor)

    expect(guest.locator(OTHER_MUTE_MARK)).to_be_visible()


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_media_state_shows_the_person_icon_for_a_person_without_a_camera(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any]
) -> None:
    host = open_person(**form_factor)
    invite_link = start_call(host, base_url)
    guest = open_person(**form_factor)
    guest.add_init_script(REFUSE_THE_CAMERA)
    guest.goto(invite_link)

    expect(host.locator(OTHER_CAMERA_OFF)).to_be_visible()
    expect(host.locator(f"{OTHER_CAMERA_OFF} .av svg")).to_be_visible()
