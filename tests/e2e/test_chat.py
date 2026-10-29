from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import expect

from tests.e2e.support.calls import open_call
from tests.e2e.support.people import FORM_FACTORS

if TYPE_CHECKING:
    from collections.abc import Callable

    from playwright.sync_api import Page

pytestmark = pytest.mark.e2e

MESSAGES_IN_A_LONG_HISTORY = 60
LIST_FILLS_PAST_ITS_BOX = "(list) => list.scrollHeight > list.clientHeight"
LIST_IS_AT_THE_BOTTOM = "(list) => list.scrollHeight - list.scrollTop - list.clientHeight < 2"
LIST_IS_AT_THE_TOP = "(list) => list.scrollTop === 0"


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_chat_with_a_long_history_scrolls_inside_the_panel_and_keeps_the_composer_in_view(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    _, guest, _ = open_call(open_person, base_url, page_errors, form_factor)
    guest.click("#chatBtn")
    for number in range(MESSAGES_IN_A_LONG_HISTORY):
        guest.fill("#chatInput", f"line {number}")
        guest.click("#chatSend")
    messages = guest.locator("#chatMessages")
    composer = guest.locator("#chatInput")
    viewport_height = guest.viewport_size["height"] if guest.viewport_size else 0

    expect(messages.locator(".chat-msg")).to_have_count(MESSAGES_IN_A_LONG_HISTORY)
    assert messages.evaluate(LIST_FILLS_PAST_ITS_BOX)
    assert messages.evaluate(LIST_IS_AT_THE_BOTTOM)
    assert composer.bounding_box()["y"] + composer.bounding_box()["height"] <= viewport_height

    messages.evaluate("(list) => { list.scrollTop = 0; }")

    assert messages.evaluate(LIST_IS_AT_THE_TOP)
    expect(messages.locator(".chat-msg").first).to_be_in_viewport()
    expect(composer).to_be_in_viewport()
