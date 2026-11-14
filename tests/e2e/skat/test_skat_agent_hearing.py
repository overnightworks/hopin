from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.skat.skat_page import expect_table_on_stage, show_table, table_link
from tests.e2e.support.calls import open_call
from tests.e2e.support.dialed_agent import AGENT_HEARING_SWITCH, AGENT_TILE, REMOVE
from tests.e2e.support.people import FORM_FACTORS

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.skat.stub_skat import StubSkat
    from tests.e2e.support.dialed_agent import Agent
    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

SWITCH = AGENT_HEARING_SWITCH
TRACK = SWITCH + ' > span[aria-hidden="true"]'
CHIP_INSET_PIXELS = 8
WHOLE_CHIP_FITS_FROM_PIXELS = 131
TOLERANCE_PIXELS = 1


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


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_switch_stays_on_a_strip_tile_while_a_table_shows_and_still_answers_a_press(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    stub_skat: StubSkat,
    page_errors: list[str],
    agent_of: Callable[[Page], Agent],
    form_factor: dict[str, Any],
) -> None:
    """CALL-57: The switch is never hidden on a strip tile: its track lies wholly inside the tile and clear of the
    remove button, the whole chip does too where the tile has the room, and a press is answered."""
    host, _guest, _ = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    agent_of(host).dial_in("Claude")
    show_table(host, table_link(stub_skat))
    expect_table_on_stage(host, stub_skat)

    switch = host.locator(SWITCH)
    expect(switch).to_be_visible()
    tile_box = box_of(AGENT_TILE, host)
    track_box = box_of(TRACK, host)
    remove_box = box_of(AGENT_TILE + " " + REMOVE, host)
    assert lies_inside(track_box, tile_box)
    assert overlap_of(track_box, remove_box) == 0
    if tile_box["width"] >= WHOLE_CHIP_FITS_FROM_PIXELS:
        chip_box = box_of(SWITCH, host)
        assert chip_box["x"] - tile_box["x"] == pytest.approx(CHIP_INSET_PIXELS, abs=TOLERANCE_PIXELS)
        assert chip_box["y"] - tile_box["y"] == pytest.approx(CHIP_INSET_PIXELS, abs=TOLERANCE_PIXELS)
        assert lies_inside(chip_box, tile_box)
        assert overlap_of(chip_box, remove_box) == 0
    switch.click()
    expect(switch).to_have_attribute("aria-checked", "false")
    switch.click()
    expect(switch).to_have_attribute("aria-checked", "true")
