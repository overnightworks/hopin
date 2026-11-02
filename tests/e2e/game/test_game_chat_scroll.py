from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.game.game_page import CARD, GAME_MASTER_LINES, TYPING, close_chat, open_chat, start_quiz
from tests.e2e.support.calls import open_call
from tests.e2e.support.page_assertions import LIST_IS_AT_THE_BOTTOM, MESSAGES_IN_A_LONG_HISTORY
from tests.e2e.support.people import FORM_FACTORS
from tests.game.support.fake_agent_service import Holds

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.server import RunningServer
    from tests.game.support.fake_agent_service import FakeAgentService

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_chat_stays_scrolled_to_the_newest_line_when_the_typing_bubble_and_the_card_render(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_service: FakeAgentService,
    form_factor: dict[str, Any],
) -> None:
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    held = Holds()
    agent_service.answer_next(held)
    open_chat(guest)
    open_chat(host)
    for number in range(MESSAGES_IN_A_LONG_HISTORY):
        guest.fill("#chatInput", f"line {number}")
        guest.click("#chatSend")
    messages = host.locator("#chatMessages")
    expect(messages.locator(".chat-msg")).to_have_count(MESSAGES_IN_A_LONG_HISTORY)
    assert messages.evaluate(LIST_IS_AT_THE_BOTTOM)

    close_chat(guest)
    start_quiz(guest)
    expect(host.locator(TYPING)).to_be_visible()
    assert messages.evaluate(LIST_IS_AT_THE_BOTTOM)
    held.release.set()

    expect(host.locator(CARD)).to_be_visible()
    expect(host.locator(GAME_MASTER_LINES)).to_have_count(1)
    assert messages.evaluate(LIST_IS_AT_THE_BOTTOM)
