from collections.abc import Callable
from typing import Any

import pytest
from playwright.sync_api import Page

from tests.e2e.conftest import MEDIA_FLOWING_TIMEOUT_MILLISECONDS, start_call

pytestmark = pytest.mark.e2e

DESKTOP = {"viewport": {"width": 1280, "height": 800}}
PHONE = {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True}
TWO_PLAYING_VIDEOS = """
() => {
    const playing = [...document.querySelectorAll("video")].filter((video) => video.videoWidth > 0);
    return playing.length === 2;
}
"""


@pytest.mark.parametrize("form_factor", [DESKTOP, PHONE], ids=["desktop", "phone"])
def test_call_two_people_join_see_each_others_video(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any]
) -> None:
    host = open_person(**form_factor)
    invite_link = start_call(host, base_url)

    guest = open_person(**form_factor)
    guest.goto(invite_link)

    for person in (host, guest):
        person.wait_for_function(TWO_PLAYING_VIDEOS, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

    guest.click("#leaveBtn")
    host.wait_for_function(
        "() => document.querySelectorAll('.video-container').length === 1",
        timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    )
