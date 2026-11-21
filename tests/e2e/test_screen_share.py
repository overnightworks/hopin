from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.support.calls import add_init_scripts, join, record_page_errors, start_call
from tests.e2e.support.media import (
    ICON_ONLY_ON_THE_TILES,
    MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    MEDIA_REQUEST_COUNT,
    OTHER_TILE,
    PICTURE_ONLY_ON_THE_TILES,
    RECORD_MEDIA_TRACKS,
    RECORD_PEER_CONNECTIONS,
    SCREEN_TILE,
    camera_track_states,
    share_the_screen,
    stop_the_screen_share,
    wait_for_peer_count,
    wait_for_the_picture,
    wait_for_the_screen_tile_to_show_the_screen,
    wait_for_video_tracks,
)
from tests.e2e.support.people import DESKTOP, FORM_FACTORS, PHONE

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.e2e

LIVE = "live"
CAMERA_BUTTON = "#videoBtn"
# The camera sender first, the screen's sender beside it; the screen's track is not in the own stream.
CAMERA_AND_SCREEN_SENT = [LIVE, f"foreign {LIVE}"]
CAMERA_SENT_AND_SCREEN_GONE = [LIVE, None]
SCREEN_SENDERS_WITHOUT_CAMERA = [None, f"foreign {LIVE}"]
SCREEN_WIDTH_MARGIN_PIXELS = 24
SHARE_CYCLES = 3
# The camera's video m-line and at most one for the screen.
MOST_VIDEO_LINES_PER_CONNECTION = 2
VIDEO_LINES_PER_OPEN_CONNECTION = """
() => window.recordedConnections
    .filter((connection) => connection.signalingState !== "closed")
    .map((connection) => connection.getTransceivers()
        .filter((transceiver) => (transceiver.sender.track || transceiver.receiver.track).kind === "video").length)
"""


@dataclass
class ShareCall:
    sharer: Page
    watcher: Page
    invite_link: str


@pytest.fixture(params=FORM_FACTORS)
def share_call(
    request: pytest.FixtureRequest, open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> ShareCall:
    """A desktop sharer, who alone has the screen button, and a watcher on each form factor."""
    sharer = record_page_errors(open_person(**DESKTOP), page_errors)
    add_init_scripts(sharer, [RECORD_MEDIA_TRACKS, RECORD_PEER_CONNECTIONS])
    invite_link = start_call(sharer, base_url)
    watcher = join(open_person, invite_link, request.param, (RECORD_PEER_CONNECTIONS,), page_errors)
    for person in (sharer, watcher):
        wait_for_peer_count(person, 1)
    wait_for_the_picture(watcher)
    return ShareCall(sharer, watcher, invite_link)


def area_of(person: Page, selector: str) -> float:
    box: dict[str, Any] = person.locator(selector).bounding_box() or {}
    return box["width"] * box["height"]


def expect_the_screen_beside_the_cameras(watcher: Page, cameras: int, cameras_off: int = 0) -> None:
    expect(watcher.locator(SCREEN_TILE)).to_have_count(1, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    wait_for_the_screen_tile_to_show_the_screen(watcher)
    expect(watcher.locator(PICTURE_ONLY_ON_THE_TILES)).to_have_count(cameras - cameras_off)
    expect(watcher.locator(OTHER_TILE)).to_have_count(cameras)


def test_a_shared_screen_is_the_largest_tile_beside_the_sharers_camera_and_goes_with_the_share(
    share_call: ShareCall,
) -> None:
    """CALL-24: A shared screen is the largest tile beside the sharer's camera and goes with the share."""
    sharer, watcher = share_call.sharer, share_call.watcher
    requests_before = sharer.evaluate(MEDIA_REQUEST_COUNT)

    share_the_screen(sharer)

    expect_the_screen_beside_the_cameras(watcher, 1)
    wait_for_video_tracks(sharer, own=[LIVE], senders=CAMERA_AND_SCREEN_SENT)
    assert area_of(watcher, SCREEN_TILE) > area_of(watcher, OTHER_TILE)
    screen_box = watcher.locator(SCREEN_TILE).bounding_box() or {}
    viewport_width = (watcher.viewport_size or {})["width"]
    assert screen_box["width"] >= viewport_width - 2 * SCREEN_WIDTH_MARGIN_PIXELS or viewport_width > 600

    stop_the_screen_share(sharer)

    expect(watcher.locator(SCREEN_TILE)).to_have_count(0, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    wait_for_the_picture(watcher)
    wait_for_video_tracks(sharer, own=[LIVE], senders=CAMERA_SENT_AND_SCREEN_GONE)
    assert camera_track_states(sharer) == [LIVE]
    assert sharer.evaluate(MEDIA_REQUEST_COUNT) == requests_before + 1

    share_the_screen(sharer)

    expect_the_screen_beside_the_cameras(watcher, 1)


def test_a_share_leaves_a_camera_that_is_off_off_and_the_screen_still_arrives(share_call: ShareCall) -> None:
    """CALL-24: A share leaves a camera that is off off and the screen still arrives."""
    sharer, watcher = share_call.sharer, share_call.watcher
    sharer.click(CAMERA_BUTTON)
    expect(watcher.locator(ICON_ONLY_ON_THE_TILES)).to_have_count(1, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

    share_the_screen(sharer)

    expect(watcher.locator(SCREEN_TILE)).to_have_count(1, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    wait_for_the_screen_tile_to_show_the_screen(watcher)
    wait_for_video_tracks(sharer, own=[], senders=SCREEN_SENDERS_WITHOUT_CAMERA)
    requests_before_the_stop = sharer.evaluate(MEDIA_REQUEST_COUNT)

    stop_the_screen_share(sharer)

    expect(watcher.locator(SCREEN_TILE)).to_have_count(0, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    expect(watcher.locator(ICON_ONLY_ON_THE_TILES)).to_have_count(1)
    assert sharer.evaluate(MEDIA_REQUEST_COUNT) == requests_before_the_stop
    expect(sharer.locator(CAMERA_BUTTON)).to_have_attribute("aria-pressed", "true")


@pytest.mark.parametrize("camera_on", [True, False], ids=["camera-on", "camera-off"])
def test_a_person_who_joins_during_a_share_sees_the_screen_and_the_camera(
    share_call: ShareCall, open_person: Callable[..., Page], base_url: str, page_errors: list[str], camera_on: bool
) -> None:
    """CALL-24: A person who joins during a share sees the screen and the camera."""
    if not camera_on:
        share_call.sharer.click(CAMERA_BUTTON)
        expect(share_call.watcher.locator(ICON_ONLY_ON_THE_TILES)).to_have_count(
            1, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS
        )
    share_the_screen(share_call.sharer)
    expect(share_call.watcher.locator(SCREEN_TILE)).to_have_count(1, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

    late = join(open_person, share_call.invite_link, DESKTOP, (RECORD_PEER_CONNECTIONS,), page_errors)

    wait_for_peer_count(late, 2)
    expect_the_screen_beside_the_cameras(late, 2, cameras_off=0 if camera_on else 1)
    expect(late.locator(ICON_ONLY_ON_THE_TILES)).to_have_count(0 if camera_on else 1)


def test_repeated_shares_show_the_screen_to_a_desktop_and_a_phone_guest_on_one_video_line_each(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    """CALL-24: Repeated shares show the screen to every guest and add no further video line per connection."""
    sharer = record_page_errors(open_person(**DESKTOP), page_errors)
    add_init_scripts(sharer, [RECORD_MEDIA_TRACKS, RECORD_PEER_CONNECTIONS])
    invite_link = start_call(sharer, base_url)
    guests = [
        join(open_person, invite_link, form, (RECORD_PEER_CONNECTIONS,), page_errors) for form in (DESKTOP, PHONE)
    ]
    everyone = [sharer, *guests]
    for person in everyone:
        wait_for_peer_count(person, 2)

    for _ in range(SHARE_CYCLES):
        share_the_screen(sharer)
        for guest in guests:
            expect(guest.locator(SCREEN_TILE)).to_have_count(1, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
            wait_for_the_screen_tile_to_show_the_screen(guest)
        stop_the_screen_share(sharer)
        for guest in guests:
            expect(guest.locator(SCREEN_TILE)).to_have_count(0, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

    share_the_screen(sharer)
    for person in everyone:
        assert max(person.evaluate(VIDEO_LINES_PER_OPEN_CONNECTION)) <= MOST_VIDEO_LINES_PER_CONNECTION
