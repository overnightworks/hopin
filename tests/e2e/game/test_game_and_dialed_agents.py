from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.game.game_page import start_quiz
from tests.e2e.support.calls import join, open_call
from tests.e2e.support.dialed_agent import AGENT_TILE, COUNT, assert_tiles_clear_of_the_controls
from tests.e2e.support.people import FORM_FACTORS
from tests.game.support.fake_agent_backend import Succeeds

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.dialed_agent import Agent
    from tests.e2e.support.server import RunningServer
    from tests.game.support.fake_agent_backend import FakeAgentBackend

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_four_people_the_quiz_master_and_two_dialed_in_agents_are_seven_tiles_and_the_controls_stay_clear(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_of: Callable[[Page], Agent],
    agent_backend: FakeAgentBackend,
    form_factor: dict[str, Any],
) -> None:
    """CALL-47: Four people, the quiz master and two agents are seven tiles, the count names people only."""
    host, guest, invite_link = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    for _ in range(2):
        join(open_person, invite_link, form_factor, (), page_errors)
    agent_backend.answer_next(Succeeds("Erste?"))

    agent_of(host).dial_in("Claude")
    agent_of(guest).dial_in("Codex")
    start_quiz(host)
    host.keyboard.press("Escape")

    expect(host.locator("#container-agent")).to_have_count(1)
    expect(host.locator(AGENT_TILE)).to_have_count(2)
    expect(host.locator(COUNT)).to_have_text("4")
    assert_tiles_clear_of_the_controls(host, 7, form_factor)
