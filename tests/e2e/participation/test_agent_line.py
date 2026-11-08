from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.support.calls import open_call
from tests.e2e.support.people import FORM_FACTORS

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.dialed_agent import Agent
    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

AGENT_LINE = "#chatMessages .chat-msg:has(.participation-agent-sender)"


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_line_an_agent_writes_is_read_by_another_seat_under_its_name_with_the_ai_mark_as_plain_text(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_of: Callable[[Page], Agent],
    form_factor: dict[str, Any],
) -> None:
    """CALL-53: An agent's line shows in the chat of every seat under the agent's name with the AI mark, as text."""
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    agent = agent_of(host)
    agent.dial_in("Claude")
    guest.click("#chatBtn")

    agent.say("Hello <b>x</b>")

    line = guest.locator(AGENT_LINE)
    expect(line).to_have_count(1)
    expect(line.locator(".participation-agent-sender")).to_contain_text("Claude")
    expect(line.locator(".participation-agent-badge")).to_be_visible()
    expect(line).to_contain_text("Hello <b>x</b>")
    expect(line.locator("b")).to_have_count(0)
