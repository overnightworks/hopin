from collections.abc import Callable
from itertools import pairwise
from typing import Any

import pytest
from playwright.sync_api import Page, ViewportSize, expect

from tests.e2e.conftest import DESKTOP, MEDIA_FLOWING_TIMEOUT_MILLISECONDS, PHONE, start_call

pytestmark = pytest.mark.e2e

POSITION_TOLERANCE_PIXELS = 1
DESKTOP_VIEWPORT = ViewportSize(**DESKTOP["viewport"])
PHONE_VIEWPORT = ViewportSize(**PHONE["viewport"])
LARGEST_CALL = 6
DESKTOP_SHAPES = {2: (2, 1), 3: (3, 1), 4: (2, 2), 5: (3, 2), 6: (3, 2)}
PHONE_SHAPES = {3: (1, 3), 4: (2, 2), 5: (2, 3), 6: (2, 3)}
PHONE_PICTURE_IN_PICTURE = {"width": 104, "height": 152, "inset_right": 14}
CAMERA_OFF_SURFACE = "rgb(37, 43, 50)"
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
SHARE_THE_SCREEN_FROM_A_CANVAS = """
navigator.mediaDevices.getDisplayMedia = async () => document.createElement("canvas").captureStream();
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
    assert boxes["self"]["bottom"] < boxes["stage"]["bottom"]


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
    own_tile = host.locator("#localContainer")
    expect(own_tile).to_have_class("tile self mirror")

    host.click("#screenBtn")
    expect(host.locator("#screenBtn")).to_have_text("Stop Sharing", timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    expect(own_tile).not_to_have_class("tile self mirror")

    host.click("#screenBtn")
    expect(host.locator("#screenBtn")).to_have_text("Share Screen", timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    expect(own_tile).to_have_class("tile self mirror")


def test_stage_with_the_camera_off_shows_the_person_icon_on_the_neutral_surface(
    open_person: Callable[..., Page], base_url: str
) -> None:
    host, _ = start_hosting(open_person, base_url)

    host.click("#videoBtn")

    placeholder = host.locator("#localPlaceholder")
    expect(placeholder.locator(".av svg")).to_be_visible()
    expect(placeholder).to_have_css("background-color", CAMERA_OFF_SURFACE)
