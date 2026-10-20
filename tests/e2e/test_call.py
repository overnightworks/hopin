from collections.abc import Callable
from typing import Any

import pytest
from playwright.sync_api import Page

from tests.e2e.conftest import DESKTOP, MEDIA_FLOWING_TIMEOUT_MILLISECONDS, PHONE, join_by_link, start_call

pytestmark = pytest.mark.e2e

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
    join_by_link(guest, invite_link)

    for person in (host, guest):
        person.wait_for_function(TWO_PLAYING_VIDEOS, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

    guest.click("#leaveBtn")
    host.wait_for_function(
        "() => document.querySelectorAll('.tile').length === 1",
        timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    )
