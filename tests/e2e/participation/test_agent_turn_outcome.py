from typing import TYPE_CHECKING, Any

import pytest
from design.words import text as kit_text
from playwright.sync_api import expect

from tests.e2e.support.calls import open_more
from tests.e2e.support.dialed_agent import AGENT_TILE, REMOVE
from tests.e2e.support.people import FORM_FACTORS

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.participation_scene import Scene

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_stated_outcome_shows_beside_the_agents_name_until_a_new_turn_and_goes_with_the_agent(
    scene_of: Callable[[dict[str, Any]], Scene], form_factor: dict[str, Any]
) -> None:
    """CALL-73: The outcome an agent states for its stopped turns shows beside its name in every browser that shows
    the control (the adder's and the host's), the latest one replaces it, a new turn clears it, and the agent's
    removal drops it."""
    scene = scene_of(form_factor)
    scene.member_adds_and_the_agent_accepts()
    for person in (scene.member, scene.host):
        open_more(person)
    rows = [scene.offer_of_agent(person) for person in (scene.member, scene.host)]

    scene.agent.state_turn(stopped="busy")

    for row in rows:
        expect(row).to_contain_text(kit_text("de", "turnState.busy"))

    scene.agent.state_turn(stopped="budget_spent")

    for row in rows:
        expect(row).to_contain_text(kit_text("de", "turnState.budgetSpent"))
        expect(row).not_to_contain_text(kit_text("de", "turnState.busy"))

    scene.agent.state_turn(thinking=True)

    for row in rows:
        expect(row).to_have_count(1)
        expect(row).not_to_contain_text(kit_text("de", "turnState.budgetSpent"))

    scene.agent.state_turn(stopped="unavailable")

    for row in rows:
        expect(row).to_contain_text(kit_text("de", "turnState.unavailable"))
    scene.host.keyboard.press("Escape")
    scene.host.locator(AGENT_TILE + " " + REMOVE).click()
    open_more(scene.host)

    for person in (scene.member, scene.host):
        expect(person.locator("#agentOffers")).not_to_contain_text(kit_text("de", "turnState.unavailable"))
