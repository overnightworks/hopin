from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.support.calls import join, open_call, open_host
from tests.e2e.support.dialed_agent import (
    AGENT_HEARING_SWITCH,
    AGENT_TILE,
    COUNT,
    REMOVE,
    TILES_ABOVE_THE_CONTROLS,
    TOLERANCE_PIXELS,
    assert_tiles_clear_of_the_controls,
)
from tests.e2e.support.media import RECORD_PEER_CONNECTIONS, wait_for_peer_count
from tests.e2e.support.people import DESKTOP, FORM_FACTORS

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.dialed_agent import Agent
    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

COUNTS_OF_PEOPLE = (1, 2, 5)


def test_a_dialed_in_agent_shows_as_a_tile_every_page_sees_and_its_sponsor_or_the_host_removes_it(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_of: Callable[[Page], Agent],
) -> None:
    """CALL-48, CALL-49, CALL-50: A dialed-in agent shows with its sponsor; its sponsor or the host removes it."""
    host, guest, invite_link = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)
    bystander = join(open_person, invite_link, DESKTOP, (RECORD_PEER_CONNECTIONS,), page_errors)
    wait_for_peer_count(bystander, 2)

    agent_of(bystander).dial_in("Claude")

    for person in (host, guest, bystander):
        tile = person.locator(AGENT_TILE)
        expect(tile).to_have_count(1)
        expect(tile.locator(".name")).to_contain_text("Claude")
        expect(tile.locator(".ai-badge")).to_be_visible()
        expect(tile.locator(".sponsor")).to_be_visible()
    expect(host.locator(COUNT)).to_have_text("3")
    expect(host.locator(AGENT_TILE + " " + REMOVE)).to_have_count(1)
    expect(bystander.locator(AGENT_TILE + " " + REMOVE)).to_have_count(1)
    expect(guest.locator(AGENT_TILE + " " + REMOVE)).to_have_count(0)
    for person in (host, guest, bystander):
        expect(person.locator(AGENT_HEARING_SWITCH)).to_have_count(0)

    bystander.locator(AGENT_TILE + " " + REMOVE).click()

    for person in (host, guest, bystander):
        expect(person.locator(AGENT_TILE)).to_have_count(0)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_four_people_and_two_dialed_in_agents_are_six_tiles_the_count_names_people_and_the_controls_stay_clear(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_of: Callable[[Page], Agent],
    form_factor: dict[str, Any],
) -> None:
    """CALL-47: Four people and two agents fill six places, the count names people only, the controls stay clear."""
    host, guest, invite_link = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    for _ in range(2):
        join(open_person, invite_link, form_factor, (), page_errors)

    agent_of(host).dial_in("Claude")
    agent_of(guest).dial_in("Codex")

    expect(host.locator("#stage")).to_have_attribute("data-count", "6")
    expect(host.locator(COUNT)).to_have_text("4")
    overhang = host.evaluate(TILES_ABOVE_THE_CONTROLS)
    assert len(overhang) == 6
    assert all(extra <= TOLERANCE_PIXELS for extra in overhang)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
@pytest.mark.parametrize("people", COUNTS_OF_PEOPLE)
def test_one_dialed_in_agent_is_one_more_tile_beside_one_two_or_five_people_and_the_controls_stay_clear(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_of: Callable[[Page], Agent],
    form_factor: dict[str, Any],
    people: int,
) -> None:
    """CALL-47: An agent is one more tile beside one, two or five people, the count names people only."""
    host, invite_link = open_host(open_person, e2e_server.base_url, page_errors, form_factor)
    for _ in range(people - 1):
        join(open_person, invite_link, form_factor, (), page_errors)

    agent_of(host).dial_in("Claude")

    expect(host.locator(AGENT_TILE)).to_have_count(1)
    expect(host.locator(COUNT)).to_have_text(str(people))
    assert_tiles_clear_of_the_controls(host, people + 1, form_factor)
