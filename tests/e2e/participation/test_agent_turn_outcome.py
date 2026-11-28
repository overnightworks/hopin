from typing import TYPE_CHECKING, Any

import pytest
from design.words import text as kit_text
from playwright.sync_api import expect

from tests.e2e.support.calls import open_more
from tests.e2e.support.dialed_agent import (
    AGENT_LISTEN_SWITCH,
    AGENT_TILE,
    TURN_STATE_ON_A_TILE,
    assert_the_pill_is_whole_and_clear_of_the_chips,
    send_away_button,
)
from tests.e2e.support.people import FORM_FACTORS, PHONE

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.participation_scene import Scene

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_stated_outcome_shows_beside_the_agents_name_for_every_seat_until_a_new_turn_and_goes_with_the_agent(
    scene_of: Callable[[dict[str, Any]], Scene], form_factor: dict[str, Any]
) -> None:
    """CALL-73: The outcome an agent states for its stopped turns shows beside its name on its tile in every browser of
    the call, a person with no right to send the agent away included, and on the here row of those who may (driven
    here in the adder's and the host's); the latest one replaces it, a new turn clears it, and the agent's removal
    drops it."""
    scene = scene_of(form_factor)
    scene.member_adds_and_the_agent_accepts()
    outsider = scene.person_joins_late()
    for person in (scene.member, scene.host):
        open_more(person)
    rows = [scene.offer_of_agent(person) for person in (scene.member, scene.host)]
    tiles = [person.locator(TURN_STATE_ON_A_TILE) for person in (*scene.everyone, outsider)]

    scene.agent.state_turn(stopped="busy")

    for row in rows:
        expect(row).to_contain_text(kit_text("de", "turnState.busy"))
    for tile in tiles:
        expect(tile).to_have_text(kit_text("de", "turnState.busy"))
    expect(scene.offers_on(outsider)).to_have_count(0)

    scene.agent.state_turn(stopped="budget_spent")

    for row in rows:
        expect(row).to_contain_text(kit_text("de", "turnState.budgetSpent"))
        expect(row).not_to_contain_text(kit_text("de", "turnState.busy"))
    for tile in tiles:
        expect(tile).to_have_text(kit_text("de", "turnState.budgetSpent"))
    for person in (*scene.everyone, outsider):
        assert_the_pill_is_whole_and_clear_of_the_chips(person, (AGENT_LISTEN_SWITCH,), word_shown=True)

    scene.agent.state_turn(thinking=True)

    for row in rows:
        expect(row).to_have_count(1)
        expect(row).not_to_contain_text(kit_text("de", "turnState.budgetSpent"))
    for tile in tiles:
        expect(tile).to_have_count(0)

    scene.agent.state_turn(stopped="unavailable")

    for row in rows:
        expect(row).to_contain_text(kit_text("de", "turnState.unavailable"))
    for tile in tiles:
        expect(tile).to_have_text(kit_text("de", "turnState.unavailable"))
    send_away_button(scene.host, scene.agent.name).click()

    for person in (*scene.everyone, outsider):
        expect(person.locator(AGENT_TILE)).to_have_count(0)
        expect(person.locator("#agentOffers")).not_to_contain_text(kit_text("de", "turnState.unavailable"))


def test_the_stated_word_stays_whole_in_the_pill_of_a_full_bleed_agent_tile_on_a_phone(
    scene_of: Callable[[dict[str, Any]], Scene],
) -> None:
    """CALL-73: On a phone with only the owner and the agent in the call, the agent's tile is full-bleed and its pill
    takes a second line, so the stated word is whole inside the pill and the tile."""
    scene = scene_of(PHONE)
    scene.member_adds_and_the_agent_accepts()
    scene.host.close()
    scene.member.close()
    scene.agent.state_turn(stopped="budget_spent")

    expect(scene.owner.locator("#stage")).to_have_attribute("data-count", "2")
    expect(scene.owner.locator(TURN_STATE_ON_A_TILE)).to_have_text(kit_text("de", "turnState.budgetSpent"))
    assert_the_pill_is_whole_and_clear_of_the_chips(scene.owner, (AGENT_LISTEN_SWITCH,), word_shown=True)
