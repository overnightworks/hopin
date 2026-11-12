from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.skat.skat_page import expect_table_on_stage, show_table, table_link
from tests.e2e.support.calls import open_call
from tests.e2e.support.dialed_agent import AGENT_TILE, REMOVE
from tests.e2e.support.people import FORM_FACTORS

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.skat.stub_skat import StubSkat
    from tests.e2e.support.dialed_agent import Agent
    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

SWITCH = AGENT_TILE + ' [role="switch"]'


@pytest.fixture
def e2e_server(skat_hearing_server: RunningServer) -> RunningServer:
    return skat_hearing_server


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
    """CALL-57: The switch is never hidden on a strip tile, clear of the remove button, and a press is answered."""
    host, _guest, _ = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    agent_of(host).dial_in("Claude")
    show_table(host, table_link(stub_skat))
    expect_table_on_stage(host, stub_skat)

    switch = host.locator(SWITCH)
    expect(switch).to_be_visible()
    tile_box = host.locator(AGENT_TILE).bounding_box()
    switch_box = switch.bounding_box()
    remove_box = host.locator(AGENT_TILE + " " + REMOVE).bounding_box()
    assert tile_box is not None
    assert switch_box is not None
    assert remove_box is not None
    assert overlap_of(switch_box, tile_box) > 0
    assert overlap_of(switch_box, remove_box) == 0
    switch.click()
    expect(switch).to_have_attribute("aria-checked", "false")
    switch.click()
    expect(switch).to_have_attribute("aria-checked", "true")
