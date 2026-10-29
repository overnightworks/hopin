from typing import TYPE_CHECKING, Any

import pytest

from hopin.config.constants import APP_ASSET_PATH, IMMUTABLE_ASSET_DIRECTORY, STATIC_PATH
from tests.e2e.support.calls import join_by_link, start_call
from tests.e2e.support.media import MEDIA_FLOWING_TIMEOUT_MILLISECONDS
from tests.e2e.support.people import DESKTOP, PHONE

if TYPE_CHECKING:
    from collections.abc import Callable

    from playwright.sync_api import Page

pytestmark = pytest.mark.e2e

TWO_PLAYING_VIDEOS = """
() => {
    const playing = [...document.querySelectorAll("video")].filter((video) => video.videoWidth > 0);
    return playing.length === 2;
}
"""

# What a call may load as a script: the served wire vocabulary and the page build's chunks, which hold the call code.
SCRIPT_PATHS = (f"{STATIC_PATH}/js/", f"{APP_ASSET_PATH}/{IMMUTABLE_ASSET_DIRECTORY}/")


def record_scripts(person: Page, scripts: list[str]) -> Page:
    person.on("request", lambda request: request.resource_type == "script" and scripts.append(request.url))
    return person


@pytest.mark.parametrize("form_factor", [DESKTOP, PHONE], ids=["desktop", "phone"])
def test_call_two_people_join_see_each_others_video_with_only_the_call_cores_modules(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any]
) -> None:
    scripts: list[str] = []
    host = record_scripts(open_person(**form_factor), scripts)
    invite_link = start_call(host, base_url)

    guest = record_scripts(open_person(**form_factor), scripts)
    join_by_link(guest, invite_link)

    for person in (host, guest):
        person.wait_for_function(TWO_PLAYING_VIDEOS, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

    guest.click("#leaveBtn")
    host.wait_for_function(
        "() => document.querySelectorAll('.tile').length === 1",
        timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    )
    assert scripts
    assert [url for url in scripts if not url.startswith(tuple(f"{base_url}{path}" for path in SCRIPT_PATHS))] == []
