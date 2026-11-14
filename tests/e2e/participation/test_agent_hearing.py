import re
from typing import TYPE_CHECKING, Any

import pytest
from design.words import text as kit_text
from playwright.sync_api import Locator, Page, expect

from tests.e2e.support.calls import join, open_call
from tests.e2e.support.dialed_agent import AGENT_HEARING_SWITCH, AGENT_TILE, REMOVE
from tests.e2e.support.media import RECORD_PEER_CONNECTIONS, wait_for_peer_count
from tests.e2e.support.people import DESKTOP, FORM_FACTORS

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.dialed_agent import Agent
    from tests.e2e.support.participation_scene import Scene
    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

SWITCH = AGENT_HEARING_SWITCH
SWITCH_NAME = kit_text("de", "hearing.agentMayHearMe")
CHIP_INSET_PIXELS = 8
REMOVE_CLEARANCE_PIXELS = 64
TOLERANCE_PIXELS = 1
MINIMUM_NON_TEXT_CONTRAST = 3
CHANNELS = re.compile(r"[\d.]+")
STYLE_COLORS = """
(chip) => {
    const track = chip.querySelector(':scope > [aria-hidden="true"]');
    const stage = getComputedStyle(document.getElementById("stage"));
    return [
        getComputedStyle(track).backgroundColor,
        getComputedStyle(chip).backgroundColor,
        stage.getPropertyValue("--stage"),
    ];
}
"""


@pytest.fixture
def e2e_server(hearing_server: RunningServer) -> RunningServer:
    return hearing_server


def box_of(locator: Locator) -> dict[str, float]:
    box = locator.bounding_box()
    assert box is not None
    return box


def luminance(rgb: tuple[float, ...]) -> float:
    linear = [
        scaled / 12.92 if scaled <= 0.03928 else ((scaled + 0.055) / 1.055) ** 2.4
        for scaled in (channel / 255 for channel in rgb)
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first: tuple[float, ...], second: tuple[float, ...]) -> float:
    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def color_of(text: str) -> tuple[float, ...]:
    """The channels of a computed `rgb()`, `rgba()` or `#rrggbb` color; a color with an alpha keeps it last."""
    text = text.strip()
    if text.startswith("#"):
        return tuple(float(int(text[index : index + 2], 16)) for index in (1, 3, 5))
    return tuple(float(channel) for channel in CHANNELS.findall(text))


def over(front: tuple[float, ...], back: tuple[float, ...]) -> tuple[float, ...]:
    *rgb, alpha = front
    return tuple(alpha * channel + (1 - alpha) * behind for channel, behind in zip(rgb, back, strict=True))


def dial_in_by_the_third_person(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_of: Callable[[Page], Agent],
    form_factor: dict[str, Any],
) -> tuple[Page, Page, Page]:
    host, guest, invite_link = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    sponsor = join(open_person, invite_link, form_factor, (RECORD_PEER_CONNECTIONS,), page_errors)
    wait_for_peer_count(sponsor, 2)
    agent_of(sponsor).dial_in("Claude")
    return host, guest, sponsor


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_sponsor_of_a_dialed_in_agent_sees_the_switch_on_where_the_picture_puts_it_and_no_one_else_does(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_of: Callable[[Page], Agent],
    form_factor: dict[str, Any],
) -> None:
    """CALL-57: The sponsor sees the switch, on, at the picture's place on the agent's tile; no other seat does."""
    host, guest, sponsor = dial_in_by_the_third_person(open_person, e2e_server, page_errors, agent_of, form_factor)

    switch = sponsor.get_by_role("switch", name=SWITCH_NAME)
    expect(switch).to_have_attribute("aria-checked", "true")
    tile_box = box_of(sponsor.locator(AGENT_TILE))
    switch_box = box_of(switch)
    assert switch_box["x"] - tile_box["x"] == pytest.approx(CHIP_INSET_PIXELS, abs=TOLERANCE_PIXELS)
    assert switch_box["y"] - tile_box["y"] == pytest.approx(CHIP_INSET_PIXELS, abs=TOLERANCE_PIXELS)
    assert switch_box["width"] <= tile_box["width"] - REMOVE_CLEARANCE_PIXELS + TOLERANCE_PIXELS
    remove_box = box_of(sponsor.locator(AGENT_TILE + " " + REMOVE))
    assert switch_box["x"] + switch_box["width"] <= remove_box["x"]
    for other in (host, guest):
        expect(other.locator(AGENT_TILE)).to_have_count(1)
        expect(other.locator(SWITCH)).to_have_count(0)


def test_a_press_is_answered_by_the_servers_record_which_the_switch_shows(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_of: Callable[[Page], Agent],
) -> None:
    """CALL-57: A press asks the server; the switch shows the answer, both ways."""
    _host, _guest, sponsor = dial_in_by_the_third_person(open_person, e2e_server, page_errors, agent_of, DESKTOP)
    switch = sponsor.locator(SWITCH)

    switch.click()
    expect(switch).to_have_attribute("aria-checked", "false")
    switch.click()
    expect(switch).to_have_attribute("aria-checked", "true")


def test_the_owner_of_an_agent_another_member_added_sees_the_switch_off_and_the_member_sees_none(
    scene_of: Callable[[dict[str, Any]], Scene],
) -> None:
    """CALL-57: The switch starts off for the sponsor when another member added the agent; only the sponsor has it."""
    scene = scene_of(DESKTOP)

    scene.member_adds_and_the_agent_accepts()

    expect(scene.owner.locator(SWITCH)).to_have_attribute("aria-checked", "false")
    for other in (scene.host, scene.member):
        expect(other.locator(AGENT_TILE)).to_have_count(1)
        expect(other.locator(SWITCH)).to_have_count(0)


def test_the_switch_stands_out_from_the_glass_over_a_dark_video(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_of: Callable[[Page], Agent],
) -> None:
    """CALL-57: The switch's on state has at least 3:1 non-text contrast against its glass over a dark video, in the
    light theme too, where the app's own accent is dark."""
    _host, _guest, sponsor = dial_in_by_the_third_person(
        open_person, e2e_server, page_errors, agent_of, {**DESKTOP, "color_scheme": "light"}
    )
    expect(sponsor.locator(SWITCH)).to_have_attribute("aria-checked", "true")

    track, glass, stage = (color_of(text) for text in sponsor.locator(SWITCH).evaluate(STYLE_COLORS))

    assert contrast(track[:3], over(glass, stage)[:3]) >= MINIMUM_NON_TEXT_CONTRAST
