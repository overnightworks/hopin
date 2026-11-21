from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.stage.stage_page import expect_table_on_stage, show_table, table_link
from tests.e2e.support.calls import open_call
from tests.e2e.support.dialed_agent import (
    AGENT_HEARING_SWITCH,
    AGENT_LISTEN_SWITCH,
    AGENT_SEE_SWITCH,
    AGENT_TILE,
    REMOVE,
)
from tests.e2e.support.people import FORM_FACTORS, PHONE

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.stage.stub_skat import StubSkat
    from tests.e2e.support.dialed_agent import Agent
    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

SWITCH = AGENT_HEARING_SWITCH
TRACK = SWITCH + ' >> span[aria-hidden="true"]'
SEE_TRACK = AGENT_SEE_SWITCH + ' >> span[aria-hidden="true"]'
SEE_ICON = AGENT_SEE_SWITCH + " >> .icon svg"
CHIP_INSET_PIXELS = 8
WHOLE_CHIP_FITS_FROM_PIXELS = 131
TOLERANCE_PIXELS = 1
MIN_TARGET_PIXELS = 24


@pytest.fixture
def e2e_server(skat_hearing_server: RunningServer) -> RunningServer:
    return skat_hearing_server


def box_of(selector: str, page: Page) -> dict[str, float]:
    box = page.locator(selector).bounding_box()
    assert box is not None
    return box


def lies_inside(inner: dict[str, float], outer: dict[str, float]) -> bool:
    return (
        inner["x"] >= outer["x"] - TOLERANCE_PIXELS
        and inner["y"] >= outer["y"] - TOLERANCE_PIXELS
        and inner["x"] + inner["width"] <= outer["x"] + outer["width"] + TOLERANCE_PIXELS
        and inner["y"] + inner["height"] <= outer["y"] + outer["height"] + TOLERANCE_PIXELS
    )


def overlap_of(first: dict[str, float], second: dict[str, float]) -> float:
    """The area two boxes share."""
    width = min(first["x"] + first["width"], second["x"] + second["width"]) - max(first["x"], second["x"])
    height = min(first["y"] + first["height"], second["y"] + second["height"]) - max(first["y"], second["y"])
    return max(width, 0) * max(height, 0)


LAYOUT_SETTLED_SCRIPT = """([tileSelector, fitsFrom]) => {
    const tile = document.querySelector(tileSelector);
    const chips = tile && tile.querySelector(".hear-set");
    return chips !== null && (tile.clientWidth < fitsFrom) === chips.classList.contains("narrow");
}"""


def wait_for_chips_to_follow_the_tile_width(host: Page) -> None:
    """The chips switch layout a frame after their tile changes width; a box read in between is of the old layout."""
    host.wait_for_function(LAYOUT_SETTLED_SCRIPT, arg=[AGENT_TILE, WHOLE_CHIP_FITS_FROM_PIXELS])


@pytest.fixture
def host_with_a_table_and_an_agent(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    stub_skat: StubSkat,
    page_errors: list[str],
    agent_of: Callable[[Page], Agent],
    form_factor: dict[str, Any],
) -> Page:
    host, _guest, _ = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    agent_of(host).dial_in("Claude")
    show_table(host, table_link(stub_skat))
    expect_table_on_stage(host, stub_skat)
    wait_for_chips_to_follow_the_tile_width(host)
    return host


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_switch_stays_on_a_strip_tile_while_a_table_shows_and_still_answers_a_press(
    host_with_a_table_and_an_agent: Page,
) -> None:
    """CALL-57: The switch is never hidden on a strip tile: its track lies wholly inside the tile and clear of the
    remove button, the whole chip does too where the tile has the room, and a press is answered."""
    host = host_with_a_table_and_an_agent
    switch = host.locator(SWITCH)
    expect(switch).to_be_visible()
    tile_box = box_of(AGENT_TILE, host)
    track_box = box_of(TRACK, host)
    remove_box = box_of(AGENT_TILE + " " + REMOVE, host)
    assert lies_inside(track_box, tile_box)
    assert overlap_of(track_box, remove_box) == 0
    narrow = tile_box["width"] < WHOLE_CHIP_FITS_FROM_PIXELS
    expect(host.locator(AGENT_LISTEN_SWITCH)).to_have_count(0 if narrow else 1)
    if not narrow:
        listen_box = box_of(AGENT_LISTEN_SWITCH, host)
        assert lies_inside(listen_box, tile_box)
        assert overlap_of(listen_box, remove_box) == 0
        assert overlap_of(listen_box, track_box) == 0
        assert overlap_of(listen_box, box_of(AGENT_SEE_SWITCH, host)) == 0
        chip_box = box_of(SWITCH, host)
        assert chip_box["x"] - tile_box["x"] == pytest.approx(CHIP_INSET_PIXELS, abs=TOLERANCE_PIXELS)
        assert chip_box["y"] - tile_box["y"] == pytest.approx(CHIP_INSET_PIXELS, abs=TOLERANCE_PIXELS)
        assert lies_inside(chip_box, tile_box)
        assert overlap_of(chip_box, remove_box) == 0
    switch.click()
    expect(switch).to_have_attribute("aria-checked", "false")
    switch.click()
    expect(switch).to_have_attribute("aria-checked", "true")


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_see_switch_stays_on_a_strip_tile_while_a_table_shows_and_the_sponsor_can_turn_it_off(
    host_with_a_table_and_an_agent: Page,
) -> None:
    """CALL-62: The sponsor can always withdraw the consent: on a strip tile the see switch's track lies wholly inside
    the tile, clear of the remove button and of the hearing switch, and a press turns it on and off again. Its eye
    icon shows on the switch, so a sighted sponsor tells it from the hearing switch."""
    host = host_with_a_table_and_an_agent
    see_switch = host.locator(AGENT_SEE_SWITCH)
    expect(see_switch).to_be_visible()
    tile_box = box_of(AGENT_TILE, host)
    see_box = box_of(SEE_TRACK, host)
    assert lies_inside(see_box, tile_box)
    assert overlap_of(see_box, box_of(AGENT_TILE + " " + REMOVE, host)) == 0
    assert overlap_of(see_box, box_of(TRACK, host)) == 0
    expect(host.locator(SEE_ICON)).to_be_visible()
    icon_box = box_of(SEE_ICON, host)
    assert lies_inside(icon_box, tile_box)
    assert overlap_of(icon_box, box_of(AGENT_TILE + " " + REMOVE, host)) == 0
    assert lies_inside(icon_box, box_of(AGENT_SEE_SWITCH, host))
    see_switch.click()
    expect(see_switch).to_have_attribute("aria-checked", "true")
    see_switch.click()
    expect(see_switch).to_have_attribute("aria-checked", "false")


PRESSABLE_SQUARE_SCRIPT = """(switchElement, tileSelector) => {
    const tile = document.querySelector(tileSelector).getBoundingClientRect();
    const columns = Math.floor(tile.width);
    const rows = Math.floor(tile.height);
    let previous = new Array(columns + 1).fill(0);
    let largest = 0;
    for (let row = 1; row <= rows; row++) {
        const current = new Array(columns + 1).fill(0);
        for (let column = 1; column <= columns; column++) {
            const hit = document.elementFromPoint(tile.x + column - 0.5, tile.y + row - 0.5);
            if (hit && switchElement.contains(hit)) {
                current[column] = 1 + Math.min(previous[column], current[column - 1], previous[column - 1]);
                largest = Math.max(largest, current[column]);
            }
        }
        previous = current;
    }
    return largest;
}"""

TOPMOST_AT_SCRIPT = """([selector, x, y]) => Boolean(document.elementFromPoint(x, y)?.closest(selector))"""


@pytest.mark.parametrize("form_factor", [pytest.param(PHONE, id="phone")])
def test_each_switch_on_a_strip_tile_is_a_touch_target_clear_of_the_remove_button_and_toggles_on_a_tap(
    host_with_a_table_and_an_agent: Page,
) -> None:
    """CALL-57, CALL-62: Under a coarse pointer a press lands on each switch of a strip tile over a square of at least
    24 by 24 px inside the tile that belongs to neither the other switch nor the remove button, which stays pressable,
    and a tap turns it on or off."""
    host = host_with_a_table_and_an_agent
    remove_box = box_of(AGENT_TILE + " " + REMOVE, host)
    remove_points = [
        (remove_box["x"] + remove_box["width"] * fx, remove_box["y"] + remove_box["height"] * fy)
        for fx in (0.2, 0.5, 0.8)
        for fy in (0.2, 0.5, 0.8)
    ]
    for x, y in remove_points:
        assert host.evaluate(TOPMOST_AT_SCRIPT, [AGENT_TILE + " " + REMOVE, x, y])
    for selector in (AGENT_HEARING_SWITCH, AGENT_SEE_SWITCH):
        switch = host.locator(selector)
        assert switch.evaluate(PRESSABLE_SQUARE_SCRIPT, AGENT_TILE) >= MIN_TARGET_PIXELS
        assert overlap_of(box_of(selector, host), remove_box) == 0
        before = switch.get_attribute("aria-checked") or ""
        switch.tap()
        expect(switch).not_to_have_attribute("aria-checked", before)
        switch.tap()
        expect(switch).to_have_attribute("aria-checked", before)
