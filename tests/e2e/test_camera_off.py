from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect

from hopin.words import text
from tests.e2e.support.calls import add_init_scripts, join, record_page_errors, start_call
from tests.e2e.support.media import (
    COUNT_OFFERS,
    EVERY_VIDEO_CAP_IS,
    ICON_ONLY_ON_THE_TILES,
    MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    OFFER_COUNT,
    ONE_CAP_PER_CONNECTION,
    PICTURE_ONLY_ON_THE_TILES,
    RECORD_MEDIA_TRACKS,
    RECORD_PEER_CONNECTIONS,
    REFUSE_THE_CAMERA,
    camera_track_states,
    inbound_per_connection,
    wait_for_inbound_growth,
    wait_for_peer_count,
    wait_for_the_light_to_go_out,
    wait_for_the_picture,
    wait_for_video_tracks,
)
from tests.e2e.support.people import DESKTOP, FORM_FACTORS

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.e2e

LIVE = "live"
CAMERA_BUTTON = "#videoBtn"
STATUS = "#status"
# About one second of Opus (20 ms packets): the first video bytes of a camera that is on arrive within
# about 0.1 s of the first audio, so a zero-video read after this many packets sees an off camera.
AUDIO_PACKETS_THAT_SPAN_FIRST_VIDEO = 50.0
RECORDED_PERSON = (RECORD_MEDIA_TRACKS, RECORD_PEER_CONNECTIONS, COUNT_OFFERS)


@dataclass
class CallWithWatcher:
    presser: Page
    watcher: Page


def start_presser(
    open_person: Callable[..., Page],
    base_url: str,
    form_factor: dict[str, Any],
    page_errors: list[str],
    scripts_before_the_recorder: tuple[str, ...] = (),
) -> tuple[Page, str]:
    presser = record_page_errors(open_person(**form_factor), page_errors)
    add_init_scripts(presser, [*scripts_before_the_recorder, *RECORDED_PERSON])
    return presser, start_call(presser, base_url, with_camera=REFUSE_THE_CAMERA not in scripts_before_the_recorder)


def start_call_with_watcher(
    open_person: Callable[..., Page],
    base_url: str,
    form_factor: dict[str, Any],
    page_errors: list[str],
    scripts_before_the_recorder: tuple[str, ...] = (),
    *,
    presser_joins: bool = False,
) -> CallWithWatcher:
    if presser_joins:
        watcher = record_page_errors(open_person(**form_factor), page_errors)
        add_init_scripts(watcher, [RECORD_PEER_CONNECTIONS, COUNT_OFFERS])
        presser_scripts = (*scripts_before_the_recorder, *RECORDED_PERSON)
        presser = join(open_person, start_call(watcher, base_url), form_factor, presser_scripts, page_errors)
    else:
        presser, invite_link = start_presser(
            open_person, base_url, form_factor, page_errors, scripts_before_the_recorder
        )
        watcher = join(open_person, invite_link, form_factor, (RECORD_PEER_CONNECTIONS, COUNT_OFFERS), page_errors)
    for person in (presser, watcher):
        wait_for_peer_count(person, 1)
    return CallWithWatcher(presser, watcher)


@pytest.fixture
def call_with_watcher(start_call_with: Callable[..., Any]) -> CallWithWatcher:
    return start_call_with(start_call_with_watcher)


@pytest.fixture
def call_with_host_watching_the_guest(start_call_with: Callable[..., Any]) -> CallWithWatcher:
    return start_call_with(start_call_with_watcher, presser_joins=True)


def press_camera_button(person: Page, *, pressed: bool) -> None:
    person.click(CAMERA_BUTTON)
    expect(person.locator(CAMERA_BUTTON)).to_have_attribute("aria-pressed", str(pressed).lower())


def turn_the_camera_off(person: Page) -> None:
    press_camera_button(person, pressed=True)


def expect_the_icon(watcher: Page) -> None:
    expect(watcher.locator(ICON_ONLY_ON_THE_TILES)).to_have_count(1)
    expect(watcher.locator(PICTURE_ONLY_ON_THE_TILES)).to_have_count(0)


def expect_the_shared_picture(watcher: Page) -> None:
    expect(watcher.locator(PICTURE_ONLY_ON_THE_TILES)).to_have_count(1, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    expect(watcher.locator(ICON_ONLY_ON_THE_TILES)).to_have_count(0)


def offers_made(call: CallWithWatcher) -> list[int]:
    return [person.evaluate(OFFER_COUNT) for person in (call.presser, call.watcher)]


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_camera_off_in_a_call_releases_the_camera_and_sends_no_picture(
    call_with_host_watching_the_guest: CallWithWatcher,
) -> None:
    """CALL-26, CALL-27: Camera off in a call releases the camera and sends no picture."""
    call_with_watcher = call_with_host_watching_the_guest
    presser, watcher = call_with_watcher.presser, call_with_watcher.watcher
    wait_for_the_picture(watcher)
    offers_before = offers_made(call_with_watcher)

    turn_the_camera_off(presser)

    assert presser.get_by_role("button", name=text("de", "call.cameraOffButton"), exact=True).is_visible()
    wait_for_the_light_to_go_out(presser)
    wait_for_video_tracks(presser, own=[], senders=[None])
    expect_the_icon(watcher)
    assert offers_made(call_with_watcher) == offers_before


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_camera_on_again_opens_the_camera_and_sends_it_without_an_offer(
    call_with_host_watching_the_guest: CallWithWatcher,
) -> None:
    """CALL-27: Camera on again opens the camera and sends it without an offer."""
    call_with_watcher = call_with_host_watching_the_guest
    presser, watcher = call_with_watcher.presser, call_with_watcher.watcher
    wait_for_the_picture(watcher)
    turn_the_camera_off(presser)
    wait_for_video_tracks(presser, own=[], senders=[None])
    offers_before = offers_made(call_with_watcher)

    press_camera_button(presser, pressed=False)

    wait_for_video_tracks(presser, own=[LIVE], senders=[LIVE])
    assert camera_track_states(presser).count(LIVE) == 1
    wait_for_the_picture(watcher)
    assert offers_made(call_with_watcher) == offers_before
    presser.wait_for_function(EVERY_VIDEO_CAP_IS, arg=ONE_CAP_PER_CONNECTION)


def test_a_later_joiner_gets_no_frame_from_a_camera_that_is_off_and_then_the_picture(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    """CALL-26: A later joiner gets no frame from a camera that is off and then the picture."""
    presser, invite_link = start_presser(open_person, base_url, DESKTOP, page_errors)
    turn_the_camera_off(presser)

    later_joiner = join(open_person, invite_link, DESKTOP, (RECORD_PEER_CONNECTIONS,), page_errors)
    wait_for_peer_count(later_joiner, 1)

    expect_the_icon(later_joiner)
    wait_for_inbound_growth(later_joiner, "audio", "packetsReceived", [AUDIO_PACKETS_THAT_SPAN_FIRST_VIDEO])
    assert inbound_per_connection(later_joiner, "video", "framesDecoded") == [0]
    assert inbound_per_connection(later_joiner, "video", "bytesReceived") == [0]
    wait_for_video_tracks(presser, own=[], senders=[None])
    press_camera_button(presser, pressed=False)
    wait_for_the_picture(later_joiner)
