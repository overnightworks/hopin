from collections.abc import Callable
from itertools import pairwise
from typing import Any

import pytest
from playwright.sync_api import Page, ViewportSize, expect

from tests.e2e.conftest import (
    DESKTOP,
    INVITE_LINK_PATTERN,
    MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    PHONE,
    press_start_call,
    start_call,
)

pytestmark = pytest.mark.e2e

POSITION_TOLERANCE_PIXELS = 1
DESKTOP_VIEWPORT = ViewportSize(**DESKTOP["viewport"])
PHONE_VIEWPORT = ViewportSize(**PHONE["viewport"])
LARGEST_CALL = 6
DESKTOP_SHAPES = {2: (2, 1), 3: (3, 1), 4: (2, 2), 5: (3, 2), 6: (3, 2)}
PHONE_SHAPES = {3: (1, 3), 4: (2, 2), 5: (2, 3), 6: (2, 3)}
PHONE_PICTURE_IN_PICTURE = {"width": 104, "height": 152, "inset_right": 14, "inset_bottom": 14}
PICTURE_IN_PICTURE_INSET_SIDE = 14
PICTURE_IN_PICTURE_INSET_TOP = 48
PICTURE_IN_PICTURE_INSET_BOTTOM_ABOVE_STAGE_BOTTOM = 6
MIRRORED = "matrix(-1, 0, 0, 1, 0, 0)"
NOT_MIRRORED = "none"
CAMERA_OFF_SURFACE = "rgb(37, 43, 50)"
PHONE_QUALITY_BARS_INSET = {"top": 50, "right": 16}
QUALITY_BAR_COUNT = 3
VIGNETTE_GRADIENT = "linear-gradient(to top"
BOXES_OF_THE_STAGE = """
() => {
    const boxOf = (element) => {
        const { x, y, width, height, right, bottom } = element.getBoundingClientRect();
        return { x, y, width, height, right, bottom };
    };
    return {
        stage: boxOf(document.getElementById("stage")),
        self: boxOf(document.getElementById("localContainer")),
        tiles: [...document.querySelectorAll("#videoGrid .tile")].map(boxOf),
    };
}
"""
BOXES_OF_THE_GUESTS_QUALITY_BARS_AND_COUNT_PILL = """
() => {
    const boxOf = (element) => {
        const { x, y, right, bottom } = element.getBoundingClientRect();
        return { x, y, right, bottom };
    };
    const other = document.querySelector("#videoGrid .tile:not(.self)");
    return {
        stage: boxOf(document.getElementById("stage")),
        bars: boxOf(other.querySelector(".connection-quality")),
        countPill: boxOf(document.getElementById("participantCount")),
    };
}
"""
ELEMENT_AT_THE_CENTER_OF_THE_OWN_TILE_IS_ITS_OWN = """
() => {
    const own = document.getElementById("localContainer");
    const { x, y, width, height } = own.getBoundingClientRect();
    return own.contains(document.elementFromPoint(x + width / 2, y + height / 2));
}
"""
STAGE_BOTTOM_IN_PIXELS = """
() => parseFloat(getComputedStyle(document.getElementById("stage")).getPropertyValue("--stage-bottom"))
"""
NO_ANIMATION_IS_RUNNING = "() => document.getAnimations().length === 0"
SHARE_THE_SCREEN_FROM_A_CANVAS = """
navigator.mediaDevices.getDisplayMedia = async () => document.createElement("canvas").captureStream();
"""
REFUSE_THE_CAMERA = """
const nativeGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
navigator.mediaDevices.getUserMedia = (constraints) => {
    if (constraints.video) return Promise.reject(new DOMException("no camera", "NotFoundError"));
    return nativeGetUserMedia(constraints);
};
"""
Box = dict[str, float]


def _number_of_distinct(positions: list[float]) -> int:
    ordered = sorted(positions)
    return 1 + sum(later - earlier > POSITION_TOLERANCE_PIXELS for earlier, later in pairwise(ordered))


def _stage_boxes(host: Page, count: int) -> dict[str, Any]:
    expect(host.locator("#stage")).to_have_attribute("data-count", str(count))
    boxes: dict[str, Any] = host.evaluate(BOXES_OF_THE_STAGE)
    assert len(boxes["tiles"]) == count
    return boxes


def _assert_inside(box: Box, stage: Box) -> None:
    assert box["x"] >= stage["x"] - POSITION_TOLERANCE_PIXELS
    assert box["y"] >= stage["y"] - POSITION_TOLERANCE_PIXELS
    assert box["right"] <= stage["right"] + POSITION_TOLERANCE_PIXELS
    assert box["bottom"] <= stage["bottom"] + POSITION_TOLERANCE_PIXELS


def _assert_fills(box: Box, stage: Box) -> None:
    for side in ("x", "y", "width", "height"):
        assert box[side] == pytest.approx(stage[side], abs=POSITION_TOLERANCE_PIXELS)


def assert_laid_out_as(host: Page, count: int, columns: int, rows: int) -> None:
    boxes = _stage_boxes(host, count)
    for tile in boxes["tiles"]:
        _assert_inside(tile, boxes["stage"])
    assert _number_of_distinct([tile["x"] for tile in boxes["tiles"]]) == columns
    assert _number_of_distinct([tile["y"] for tile in boxes["tiles"]]) == rows


def assert_wide_tiles(host: Page, count: int) -> None:
    for tile in _stage_boxes(host, count)["tiles"]:
        assert tile["width"] / tile["height"] == pytest.approx(16 / 9, rel=0.01)


def assert_other_tile_fills_stage_beside_the_picture_in_picture(host: Page) -> None:
    boxes = _stage_boxes(host, 2)
    other = next(tile for tile in boxes["tiles"] if tile != boxes["self"])
    _assert_fills(other, boxes["stage"])
    assert boxes["self"]["width"] == pytest.approx(PHONE_PICTURE_IN_PICTURE["width"], abs=POSITION_TOLERANCE_PIXELS)
    assert boxes["self"]["height"] == pytest.approx(PHONE_PICTURE_IN_PICTURE["height"], abs=POSITION_TOLERANCE_PIXELS)
    assert boxes["stage"]["right"] - boxes["self"]["right"] == pytest.approx(
        PHONE_PICTURE_IN_PICTURE["inset_right"], abs=POSITION_TOLERANCE_PIXELS
    )
    assert boxes["stage"]["bottom"] - boxes["self"]["bottom"] == pytest.approx(
        PHONE_PICTURE_IN_PICTURE["inset_bottom"], abs=POSITION_TOLERANCE_PIXELS
    )
    assert host.evaluate(ELEMENT_AT_THE_CENTER_OF_THE_OWN_TILE_IS_ITS_OWN)


def start_hosting(open_person: Callable[..., Page], base_url: str) -> tuple[Page, str]:
    host = open_person(**DESKTOP)
    return host, start_call(host, base_url)


def join(open_person: Callable[..., Page], invite_link: str) -> Page:
    guest = open_person(**DESKTOP)
    guest.goto(invite_link)
    return guest


def test_stage_alone_fills_the_stage_with_the_own_tile(open_person: Callable[..., Page], base_url: str) -> None:
    host, _ = start_hosting(open_person, base_url)

    for viewport in (DESKTOP_VIEWPORT, PHONE_VIEWPORT):
        host.set_viewport_size(viewport)
        boxes = _stage_boxes(host, 1)
        _assert_fills(boxes["self"], boxes["stage"])


def test_stage_with_two_people_shows_wide_tiles_on_desktop_and_a_picture_in_picture_on_a_phone(
    open_person: Callable[..., Page], base_url: str
) -> None:
    host, invite_link = start_hosting(open_person, base_url)
    join(open_person, invite_link)

    assert_laid_out_as(host, 2, *DESKTOP_SHAPES[2])
    assert_wide_tiles(host, 2)
    host.set_viewport_size(PHONE_VIEWPORT)
    assert_other_tile_fills_stage_beside_the_picture_in_picture(host)


def test_stage_with_two_people_on_a_phone_puts_the_guests_quality_bars_below_the_count_pill(
    open_person: Callable[..., Page], base_url: str
) -> None:
    host, invite_link = start_hosting(open_person, base_url)
    join(open_person, invite_link)
    host.set_viewport_size(PHONE_VIEWPORT)
    _stage_boxes(host, 2)

    boxes = host.evaluate(BOXES_OF_THE_GUESTS_QUALITY_BARS_AND_COUNT_PILL)

    assert boxes["bars"]["y"] - boxes["stage"]["y"] == pytest.approx(
        PHONE_QUALITY_BARS_INSET["top"], abs=POSITION_TOLERANCE_PIXELS
    )
    assert boxes["stage"]["right"] - boxes["bars"]["right"] == pytest.approx(
        PHONE_QUALITY_BARS_INSET["right"], abs=POSITION_TOLERANCE_PIXELS
    )
    assert boxes["bars"]["y"] >= boxes["countPill"]["bottom"]


def test_stage_shows_every_remote_tile_a_quality_pill_of_bare_bars_and_a_vignette_over_its_video(
    open_person: Callable[..., Page], base_url: str
) -> None:
    host, invite_link = start_hosting(open_person, base_url)
    join(open_person, invite_link)
    join(open_person, invite_link)
    _stage_boxes(host, 3)

    remote_pills = host.locator("#videoGrid .tile:not(.self) .connection-quality")
    expect(remote_pills).to_have_count(2)
    for pill in remote_pills.all():
        expect(pill.locator(".quality-bar")).to_have_count(QUALITY_BAR_COUNT)
        assert pill.get_attribute("style") is None
        assert all(bar.get_attribute("style") is None for bar in pill.locator(".quality-bar").all())
    vignette = host.locator("#videoGrid .tile").first.evaluate(
        "(tile) => getComputedStyle(tile, '::after').backgroundImage"
    )
    assert vignette.startswith(VIGNETTE_GRADIENT)


def test_stage_lays_out_each_call_size_from_two_to_six_in_the_pictures_shape(
    open_person: Callable[..., Page], base_url: str
) -> None:
    host, invite_link = start_hosting(open_person, base_url)

    for count in range(2, LARGEST_CALL + 1):
        join(open_person, invite_link)
        host.set_viewport_size(DESKTOP_VIEWPORT)
        assert_laid_out_as(host, count, *DESKTOP_SHAPES[count])
        assert_wide_tiles(host, count)
        expect(host.locator("#participantCount .num")).to_have_text(str(count))
        if count in PHONE_SHAPES:
            host.set_viewport_size(PHONE_VIEWPORT)
            assert_laid_out_as(host, count, *PHONE_SHAPES[count])


def test_stage_unmirrors_the_own_screen_and_mirrors_the_camera_again(
    open_person: Callable[..., Page], base_url: str
) -> None:
    host = open_person(**DESKTOP)
    host.add_init_script(SHARE_THE_SCREEN_FROM_A_CANVAS)
    start_call(host, base_url)
    own_video = host.locator("#localVideo")
    expect(own_video).to_have_css("transform", MIRRORED)

    host.click("#screenBtn")
    expect(host.locator("#screenBtn")).to_have_text("Stop Sharing", timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    expect(own_video).to_have_css("transform", NOT_MIRRORED)

    host.click("#screenBtn")
    expect(host.locator("#screenBtn")).to_have_text("Share Screen", timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    expect(own_video).to_have_css("transform", MIRRORED)


def test_stage_with_the_camera_off_shows_the_person_icon_on_the_neutral_surface(
    open_person: Callable[..., Page], base_url: str
) -> None:
    host, _ = start_hosting(open_person, base_url)

    host.click("#videoBtn")

    placeholder = host.locator("#localPlaceholder")
    expect(placeholder.locator(".av svg")).to_be_visible()
    expect(placeholder).to_have_css("background-color", CAMERA_OFF_SURFACE)


def test_stage_without_a_camera_shows_the_person_icon_on_the_own_tile(
    open_person: Callable[..., Page], base_url: str
) -> None:
    host = open_person(**DESKTOP)
    host.add_init_script(REFUSE_THE_CAMERA)

    press_start_call(host, base_url)

    expect(host.locator("#shareUrl")).to_have_value(INVITE_LINK_PATTERN)
    placeholder = host.locator("#localPlaceholder")
    expect(placeholder.locator(".av svg")).to_be_visible()
    expect(placeholder).to_have_css("background-color", CAMERA_OFF_SURFACE)


def _drag_own_tile_to(host: Page, stage: Box, corner: str) -> None:
    host.wait_for_function(NO_ANIMATION_IS_RUNNING)
    own = host.locator("#localContainer").bounding_box()
    assert own is not None
    target_x = stage["x"] + stage["width"] * (0.25 if corner[1] == "l" else 0.75)
    target_y = stage["y"] + stage["height"] * (0.25 if corner[0] == "t" else 0.75)
    host.mouse.move(own["x"] + own["width"] / 2, own["y"] + own["height"] / 2)
    host.mouse.down()
    host.mouse.move(target_x, target_y, steps=5)
    host.mouse.up()
    host.wait_for_function(NO_ANIMATION_IS_RUNNING)


def _assert_own_tile_sits_in(host: Page, corner: str) -> None:
    host.wait_for_function(NO_ANIMATION_IS_RUNNING)
    boxes: dict[str, Any] = host.evaluate(BOXES_OF_THE_STAGE)
    stage, own = boxes["stage"], boxes["self"]
    stage_bottom = host.evaluate(STAGE_BOTTOM_IN_PIXELS)
    if corner[1] == "l":
        assert own["x"] - stage["x"] == pytest.approx(PICTURE_IN_PICTURE_INSET_SIDE, abs=POSITION_TOLERANCE_PIXELS)
    else:
        assert stage["right"] - own["right"] == pytest.approx(
            PICTURE_IN_PICTURE_INSET_SIDE, abs=POSITION_TOLERANCE_PIXELS
        )
    if corner[0] == "t":
        assert own["y"] - stage["y"] == pytest.approx(PICTURE_IN_PICTURE_INSET_TOP, abs=POSITION_TOLERANCE_PIXELS)
    else:
        assert stage["bottom"] - own["bottom"] == pytest.approx(
            stage_bottom + PICTURE_IN_PICTURE_INSET_BOTTOM_ABOVE_STAGE_BOTTOM, abs=POSITION_TOLERANCE_PIXELS
        )


def _start_hosting_on_a_phone_with_one_guest(open_person: Callable[..., Page], base_url: str) -> tuple[Page, str]:
    host = open_person(**PHONE)
    invite_link = start_call(host, base_url)
    join(open_person, invite_link)
    expect(host.locator("#stage")).to_have_attribute("data-count", "2")
    return host, invite_link


@pytest.mark.parametrize("corner", ["tl", "tr", "bl", "br"])
def test_stage_snaps_the_phone_picture_in_picture_to_the_corner_it_is_dragged_towards(
    open_person: Callable[..., Page], base_url: str, corner: str
) -> None:
    host, _ = _start_hosting_on_a_phone_with_one_guest(open_person, base_url)

    _drag_own_tile_to(host, _stage_boxes(host, 2)["stage"], corner)

    _assert_own_tile_sits_in(host, corner)


def test_stage_keeps_the_dragged_corner_while_people_join_and_leave(
    open_person: Callable[..., Page], base_url: str
) -> None:
    host, invite_link = _start_hosting_on_a_phone_with_one_guest(open_person, base_url)
    _drag_own_tile_to(host, _stage_boxes(host, 2)["stage"], "tl")

    third = join(open_person, invite_link)
    expect(host.locator("#stage")).to_have_attribute("data-count", "3")
    third.close()

    expect(host.locator("#stage")).to_have_attribute("data-count", "2")
    _assert_own_tile_sits_in(host, "tl")


def test_stage_does_not_move_the_own_tile_when_it_is_dragged_on_desktop(
    open_person: Callable[..., Page], base_url: str
) -> None:
    host, invite_link = start_hosting(open_person, base_url)
    join(open_person, invite_link)
    before = _stage_boxes(host, 2)

    _drag_own_tile_to(host, before["stage"], "tl")

    assert _stage_boxes(host, 2)["self"] == before["self"]


def test_stage_lands_the_phone_picture_in_picture_without_a_glide_when_motion_is_reduced(
    open_person: Callable[..., Page], base_url: str
) -> None:
    host = open_person(**PHONE, reduced_motion="reduce")
    join(open_person, start_call(host, base_url))
    expect(host.locator("#stage")).to_have_attribute("data-count", "2")
    host.wait_for_function(NO_ANIMATION_IS_RUNNING)

    host.evaluate("document.getElementById('localContainer').dataset.corner = 'tl'")

    assert host.evaluate(NO_ANIMATION_IS_RUNNING)
    _assert_own_tile_sits_in(host, "tl")
