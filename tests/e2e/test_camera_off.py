from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.support.calls import add_init_scripts, join, record_page_errors, start_call
from tests.e2e.support.media import (
    AFTER_PENDING_REJECTIONS_ARE_REPORTED,
    COUNT_OFFERS,
    EVERY_VIDEO_CAP_IS,
    HOLD_CAMERA_REQUESTS,
    HOLD_SCREEN_REQUESTS,
    ICON_ONLY_ON_THE_TILES,
    LATE_CAMERA_IS_STOPPED,
    MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    MEDIA_REQUEST_COUNT,
    MEDIA_REQUEST_IS_HELD,
    OFFER_COUNT,
    ONE_CAP_PER_CONNECTION,
    PICTURE_ONLY_ON_THE_TILES,
    RECORD_MEDIA_TRACKS,
    RECORD_PEER_CONNECTIONS,
    REFUSE_THE_CAMERA,
    REJECT_MEDIA,
    RELEASE_MEDIA,
    camera_track_states,
    inbound_per_connection,
    share_the_screen,
    stop_the_screen_share,
    wait_for_inbound_growth,
    wait_for_peer_count,
    wait_for_the_light_to_go_out,
    wait_for_the_picture,
    wait_for_the_tile_to_show_the_screen,
    wait_for_video_tracks,
)
from tests.e2e.support.people import DESKTOP, FORM_FACTORS

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.e2e

LIVE = "live"
CAMERA_BUTTON = "#videoBtn"
SCREEN_BUTTON = "#screenBtn"
STATUS = "#status"
OWN_CAMERA_OFF = "#localPlaceholder:not(.hidden)"
CAMERA_ERROR_STATUS = "No camera found (NotFoundError)"
IN_CALL_STATUS = "In call"
# About one second of Opus (20 ms packets): the first video bytes of a camera that is on arrive within
# about 0.1 s of the first audio, so a zero-video read after this many packets sees an off camera.
AUDIO_PACKETS_THAT_SPAN_FIRST_VIDEO = 50.0
RECORDED_PERSON = (RECORD_MEDIA_TRACKS, RECORD_PEER_CONNECTIONS, COUNT_OFFERS)


@dataclass
class CallWithWatcher:
    presser: Page
    watcher: Page


@pytest.fixture
def form_factor() -> dict[str, Any]:
    return DESKTOP


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
def call_with_watcher(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any], page_errors: list[str]
) -> CallWithWatcher:
    return start_call_with_watcher(open_person, base_url, form_factor, page_errors)


@pytest.fixture
def call_with_host_watching_the_guest(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any], page_errors: list[str]
) -> CallWithWatcher:
    return start_call_with_watcher(open_person, base_url, form_factor, page_errors, presser_joins=True)


@pytest.fixture
def call_with_watcher_and_no_camera(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any], page_errors: list[str]
) -> CallWithWatcher:
    return start_call_with_watcher(open_person, base_url, form_factor, page_errors, (REFUSE_THE_CAMERA,))


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
    call_with_watcher = call_with_host_watching_the_guest
    presser, watcher = call_with_watcher.presser, call_with_watcher.watcher
    wait_for_the_picture(watcher)
    offers_before = offers_made(call_with_watcher)

    turn_the_camera_off(presser)

    assert presser.get_by_role("button", name="Camera off", exact=True).is_visible()
    wait_for_the_light_to_go_out(presser)
    wait_for_video_tracks(presser, own=[], senders=[None])
    expect_the_icon(watcher)
    assert offers_made(call_with_watcher) == offers_before


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_camera_on_again_opens_the_camera_and_sends_it_without_an_offer(
    call_with_host_watching_the_guest: CallWithWatcher,
) -> None:
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


def test_camera_off_during_a_share_keeps_the_share_and_leaves_the_camera_off_when_it_ends(
    call_with_watcher: CallWithWatcher,
) -> None:
    presser, watcher = call_with_watcher.presser, call_with_watcher.watcher
    wait_for_the_picture(watcher)
    share_the_screen(presser)
    expect_the_shared_picture(watcher)

    turn_the_camera_off(presser)

    expect(presser.locator(SCREEN_BUTTON)).to_have_attribute("aria-pressed", "true")
    wait_for_the_light_to_go_out(presser)
    wait_for_video_tracks(presser, own=[LIVE], senders=[LIVE])
    expect_the_shared_picture(watcher)
    wait_for_the_tile_to_show_the_screen(watcher)
    cameras_before_the_stop = camera_track_states(presser)

    stop_the_screen_share(presser)

    wait_for_video_tracks(presser, own=[], senders=[None])
    assert camera_track_states(presser) == cameras_before_the_stop
    expect_the_icon(watcher)


def test_camera_on_during_a_share_opens_no_camera_until_the_share_ends(call_with_watcher: CallWithWatcher) -> None:
    presser, watcher = call_with_watcher.presser, call_with_watcher.watcher
    turn_the_camera_off(presser)
    share_the_screen(presser)
    cameras_before = camera_track_states(presser)

    press_camera_button(presser, pressed=False)

    assert camera_track_states(presser) == cameras_before
    wait_for_the_light_to_go_out(presser)
    expect_the_shared_picture(watcher)

    stop_the_screen_share(presser)

    wait_for_video_tracks(presser, own=[LIVE], senders=[LIVE])
    wait_for_the_picture(watcher)


def test_a_share_without_a_camera_ends_without_asking_for_one(call_with_watcher_and_no_camera: CallWithWatcher) -> None:
    presser, watcher = call_with_watcher_and_no_camera.presser, call_with_watcher_and_no_camera.watcher
    share_the_screen(presser)
    expect_the_shared_picture(watcher)
    requests_before_the_stop = presser.evaluate(MEDIA_REQUEST_COUNT)

    stop_the_screen_share(presser)

    wait_for_video_tracks(presser, own=[], senders=[None])
    assert presser.evaluate(MEDIA_REQUEST_COUNT) == requests_before_the_stop
    expect_the_icon(watcher)


def test_a_camera_that_cannot_reopen_when_the_share_ends_leaves_the_camera_off_and_says_so(
    call_with_watcher_and_no_camera: CallWithWatcher,
) -> None:
    presser, watcher = call_with_watcher_and_no_camera.presser, call_with_watcher_and_no_camera.watcher
    share_the_screen(presser)
    press_camera_button(presser, pressed=False)

    stop_the_screen_share(presser)

    expect(presser.locator(CAMERA_BUTTON)).to_have_attribute("aria-pressed", "true")
    wait_for_video_tracks(presser, own=[], senders=[None])
    expect(presser.locator(STATUS)).to_have_text(CAMERA_ERROR_STATUS)
    expect(presser.locator(STATUS)).to_have_text(IN_CALL_STATUS)
    expect_the_icon(watcher)
    expect(presser.locator(OWN_CAMERA_OFF)).to_be_visible()


def wait_for_the_late_camera_to_be_stopped(person: Page, cameras_before: int) -> None:
    person.wait_for_function(
        LATE_CAMERA_IS_STOPPED,
        arg=cameras_before,
        timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    )


def test_a_camera_that_arrives_after_camera_off_at_the_end_of_a_share_is_stopped(
    call_with_watcher: CallWithWatcher,
) -> None:
    presser, watcher = call_with_watcher.presser, call_with_watcher.watcher
    share_the_screen(presser)
    cameras_before = len(camera_track_states(presser))
    presser.evaluate(HOLD_CAMERA_REQUESTS)
    presser.click(SCREEN_BUTTON)
    presser.wait_for_function(MEDIA_REQUEST_IS_HELD)

    turn_the_camera_off(presser)
    presser.evaluate(RELEASE_MEDIA)

    wait_for_the_late_camera_to_be_stopped(presser, cameras_before)
    expect(presser.locator(SCREEN_BUTTON)).to_have_attribute("aria-pressed", "false")
    wait_for_video_tracks(presser, own=[], senders=[None])
    expect_the_icon(watcher)


def test_a_camera_that_arrives_after_a_share_started_is_stopped_and_the_share_stays(
    call_with_watcher: CallWithWatcher,
) -> None:
    presser, watcher = call_with_watcher.presser, call_with_watcher.watcher
    turn_the_camera_off(presser)
    cameras_before = len(camera_track_states(presser))
    presser.evaluate(HOLD_CAMERA_REQUESTS)
    presser.click(CAMERA_BUTTON)
    presser.wait_for_function(MEDIA_REQUEST_IS_HELD)

    share_the_screen(presser)
    presser.evaluate(RELEASE_MEDIA)

    wait_for_the_late_camera_to_be_stopped(presser, cameras_before)
    wait_for_video_tracks(presser, own=[LIVE], senders=[LIVE])
    expect(presser.locator(CAMERA_BUTTON)).to_have_attribute("aria-pressed", "true")
    expect_the_shared_picture(watcher)


@pytest.mark.parametrize("camera_on_while_the_picker_is_open", [False, True], ids=["camera-off", "camera-on"])
def test_a_camera_press_does_not_overtake_the_screen_picker(
    call_with_watcher: CallWithWatcher, camera_on_while_the_picker_is_open: bool
) -> None:
    presser, watcher = call_with_watcher.presser, call_with_watcher.watcher
    if camera_on_while_the_picker_is_open:
        turn_the_camera_off(presser)
    presser.evaluate(HOLD_SCREEN_REQUESTS)
    presser.click(SCREEN_BUTTON)
    presser.wait_for_function(MEDIA_REQUEST_IS_HELD)

    press_camera_button(presser, pressed=camera_on_while_the_picker_is_open is False)
    presser.evaluate(RELEASE_MEDIA)

    expect(presser.locator(SCREEN_BUTTON)).to_have_attribute("aria-pressed", "true")
    wait_for_video_tracks(presser, own=[LIVE], senders=[LIVE])
    wait_for_the_light_to_go_out(presser)
    expect_the_shared_picture(watcher)


@pytest.mark.parametrize("overtaken_by_a_share", [False, True], ids=["camera-off-pressed", "share-adopted"])
def test_a_camera_request_that_fails_after_a_newer_choice_changes_nothing(
    call_with_watcher: CallWithWatcher, overtaken_by_a_share: bool
) -> None:
    presser, watcher = call_with_watcher.presser, call_with_watcher.watcher
    if overtaken_by_a_share:
        turn_the_camera_off(presser)
        presser.evaluate(HOLD_CAMERA_REQUESTS)
        presser.click(CAMERA_BUTTON)
        presser.wait_for_function(MEDIA_REQUEST_IS_HELD)
        share_the_screen(presser)
    else:
        share_the_screen(presser)
        presser.evaluate(HOLD_CAMERA_REQUESTS)
        presser.click(SCREEN_BUTTON)
        presser.wait_for_function(MEDIA_REQUEST_IS_HELD)
        turn_the_camera_off(presser)

    presser.evaluate(REJECT_MEDIA)
    presser.evaluate(AFTER_PENDING_REJECTIONS_ARE_REPORTED)

    assert presser.locator(STATUS).text_content() == IN_CALL_STATUS
    expect(presser.locator(CAMERA_BUTTON)).to_have_attribute("aria-pressed", "true")
    if overtaken_by_a_share:
        wait_for_video_tracks(presser, own=[LIVE], senders=[LIVE])
        expect_the_shared_picture(watcher)
    else:
        wait_for_video_tracks(presser, own=[], senders=[None])
        expect_the_icon(watcher)
