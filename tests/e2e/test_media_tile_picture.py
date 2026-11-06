from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, Any, TypedDict

import pytest
from playwright.sync_api import Page, expect
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from tests.e2e.support.camera_less_call import (
    CameraLessCall,
    renegotiate_the_video_direction,
    start_camera_less_call,
    start_camera_less_host_call,
)
from tests.e2e.support.media import (
    ICON_ONLY_ON_THE_TILES,
    MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    OTHER_VIDEO,
    PICTURE_ONLY_ON_THE_TILES,
    turn_the_camera_on,
    wait_for_the_picture,
)

if TYPE_CHECKING:
    from collections.abc import Callable

pytest_plugins = ["tests.e2e.support.camera_less_call"]
pytestmark = pytest.mark.e2e


WATCH_THE_TILE_PICTURE = """
([videoSelector, shownVideoSelector, sampleIntervalMilliseconds, pictureFloor]) => {
    const video = document.querySelector(videoSelector);
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d", { willReadFrequently: true });
    const drawsAPicture = () => {
        context.clearRect(0, 0, canvas.width, canvas.height);
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
        const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
        let brightness = 0;
        for (let index = 0; index < pixels.length; index += 4) {
            brightness += pixels[index] + pixels[index + 1] + pixels[index + 2];
        }
        return brightness / (canvas.width * canvas.height * 3) > pictureFloor;
    };
    const showsTheVideo = () => video.matches(shownVideoSelector);
    const watchStartedAt = performance.now();
    const watch = { shown: [], samples: [] };
    new MutationObserver(() => {
        if (showsTheVideo()) watch.shown.push(drawsAPicture());
    }).observe(video, { attributeFilter: ["class"] });
    watch.sampler = setInterval(
        () => watch.samples.push({
            at: performance.now() - watchStartedAt,
            shows: showsTheVideo(),
            picture: drawsAPicture(),
        }),
        sampleIntervalMilliseconds,
    );
    window.tilePictureWatch = watch;
}
"""
THE_LAST_SAMPLES_SHOW_THE_PICTURE = """
(lastCount) => {
    const last = window.tilePictureWatch.samples.slice(-lastCount);
    return last.length === lastCount && last.every((sample) => sample.shows && sample.picture);
}
"""
STOP_THE_TILE_PICTURE_WATCH = """
() => {
    const watch = window.tilePictureWatch;
    clearInterval(watch.sampler);
    return { shown: watch.shown, samples: watch.samples };
}
"""
TILE_SAMPLE_INTERVAL_MILLISECONDS = 5
PICTURE_BRIGHTNESS_FLOOR = 16
LAST_SAMPLES_WITH_THE_PICTURE = 3
THROTTLED_TIMER_INTERVAL_MILLISECONDS = 1_000
MAXIMUM_SAMPLE_GAP_MILLISECONDS = THROTTLED_TIMER_INTERVAL_MILLISECONDS // 10


@dataclass
class WatchedCameraLessPerson:
    watcher: Page
    person: Page


class TileSample(TypedDict):
    at: float
    shows: bool
    picture: bool


@dataclass
class TilePictureWatch:
    shown: list[bool]
    samples: list[TileSample]


def _the_host_watches_the_guest(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any], page_errors: list[str]
) -> WatchedCameraLessPerson:
    call = start_camera_less_call(open_person, base_url, form_factor, page_errors)
    return WatchedCameraLessPerson(watcher=call.host, person=call.guest)


def _the_later_joiner_watches_the_host(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any], page_errors: list[str]
) -> WatchedCameraLessPerson:
    call = start_camera_less_host_call(open_person, base_url, form_factor, page_errors)
    return WatchedCameraLessPerson(watcher=call.later_joiner, person=call.host)


@pytest.fixture(
    params=[
        pytest.param(_the_host_watches_the_guest, id="offered"),
        pytest.param(_the_later_joiner_watches_the_host, id="answered"),
    ]
)
def watched_camera_less_person(
    request: pytest.FixtureRequest,
    open_person: Callable[..., Page],
    base_url: str,
    form_factor: dict[str, Any],
    page_errors: list[str],
) -> WatchedCameraLessPerson:
    start_the_watched_call: Callable[..., WatchedCameraLessPerson] = request.param
    return start_the_watched_call(open_person, base_url, form_factor, page_errors)


def _watch_the_tile_picture(watcher: Page) -> None:
    watcher.bring_to_front()
    watcher.evaluate(
        WATCH_THE_TILE_PICTURE,
        [OTHER_VIDEO, PICTURE_ONLY_ON_THE_TILES, TILE_SAMPLE_INTERVAL_MILLISECONDS, PICTURE_BRIGHTNESS_FLOOR],
    )


def _stop_the_tile_picture_watch(watcher: Page) -> TilePictureWatch:
    return TilePictureWatch(**watcher.evaluate(STOP_THE_TILE_PICTURE_WATCH))


def _stop_the_tile_picture_watch_once_the_picture_settles(watcher: Page) -> TilePictureWatch:
    try:
        watcher.wait_for_function(
            THE_LAST_SAMPLES_SHOW_THE_PICTURE,
            arg=LAST_SAMPLES_WITH_THE_PICTURE,
            timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
        )
    except PlaywrightTimeoutError as timeout:
        samples = _stop_the_tile_picture_watch(watcher).samples
        last_samples = samples[-LAST_SAMPLES_WITH_THE_PICTURE:]
        raise AssertionError(
            f"the tile picture did not settle: the last {LAST_SAMPLES_WITH_THE_PICTURE} of {len(samples)} samples must "
            f"show the picture, they were {last_samples}"
        ) from timeout
    return _stop_the_tile_picture_watch(watcher)


def _expect_the_sampler_never_throttled(samples: list[TileSample]) -> None:
    gaps = [later["at"] - earlier["at"] for earlier, later in pairwise(samples)]
    largest_gap = max(gaps)
    before = gaps.index(largest_gap)
    assert largest_gap < MAXIMUM_SAMPLE_GAP_MILLISECONDS, (
        f"the sampler paused {largest_gap:.0f} ms, at most {MAXIMUM_SAMPLE_GAP_MILLISECONDS} ms may pass: between "
        f"samples {before} and {before + 1} of {len(samples)}, {samples[before]['at']:.0f} and "
        f"{samples[before + 1]['at']:.0f} ms into the watch"
    )


def _expect_the_video_shown_only_with_a_picture(watch: TilePictureWatch) -> None:
    assert watch.shown, "the tile never turned to its video while watched"
    assert all(watch.shown), f"the tile showed its video before it could draw a frame: {watch.shown}"
    _expect_the_sampler_never_throttled(watch.samples)
    shown_samples = [sample["picture"] for sample in watch.samples if sample["shows"]]
    assert all(shown_samples), f"a sample showed the video without a picture: {shown_samples}"


def test_a_camera_turned_on_mid_call_turns_the_icon_into_the_picture_with_no_empty_frame_between(
    watched_camera_less_person: WatchedCameraLessPerson,
) -> None:
    watcher = watched_camera_less_person.watcher
    _watch_the_tile_picture(watcher)

    turn_the_camera_on(watched_camera_less_person.person)

    wait_for_the_picture(watcher)
    _expect_the_video_shown_only_with_a_picture(_stop_the_tile_picture_watch_once_the_picture_settles(watcher))


def test_a_video_track_that_mutes_unannounced_shows_the_icon_then_a_picture_with_no_empty_frame_and_camera_off_the_icon(
    camera_less_call: CameraLessCall,
) -> None:
    host, guest = camera_less_call.host, camera_less_call.guest
    _watch_the_tile_picture(host)
    turn_the_camera_on(guest)
    wait_for_the_picture(host)

    renegotiate_the_video_direction(guest, host, "inactive")
    expect(host.locator(ICON_ONLY_ON_THE_TILES)).to_be_visible()
    expect(host.locator(PICTURE_ONLY_ON_THE_TILES)).to_have_count(0)
    expect(guest.locator("#videoBtn")).to_have_attribute("aria-pressed", "false")
    renegotiate_the_video_direction(guest, host, "sendrecv")
    wait_for_the_picture(host)

    _expect_the_video_shown_only_with_a_picture(_stop_the_tile_picture_watch_once_the_picture_settles(host))
    guest.click("#videoBtn")
    expect(host.locator(ICON_ONLY_ON_THE_TILES)).to_be_visible()
    expect(host.locator(PICTURE_ONLY_ON_THE_TILES)).to_have_count(0)
