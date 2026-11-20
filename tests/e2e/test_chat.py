import re
from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import expect

from hopin.words import text
from tests.e2e.support.calls import join, open_call, open_host, say_in_chat
from tests.e2e.support.media import RECORD_PEER_CONNECTIONS, wait_for_peer_count
from tests.e2e.support.page_assertions import LIST_IS_AT_THE_BOTTOM, MESSAGES_IN_A_LONG_HISTORY
from tests.e2e.support.people import DESKTOP, FORM_FACTORS
from tests.e2e.support.signaling import RECORD_SOCKET_FRAMES, SOCKET_FRAMES

if TYPE_CHECKING:
    from collections.abc import Callable

    from playwright.sync_api import Page

pytestmark = pytest.mark.e2e

LINE_LIMIT = 600
SMILEY = "\N{GRINNING FACE}"
SENT_LINE = "#chatMessages .chat-msg.self"
HEARD_LINE = "#chatMessages .chat-msg.other"
LIST_FILLS_PAST_ITS_BOX = "(list) => list.scrollHeight > list.clientHeight"
LIST_IS_AT_THE_TOP = "(list) => list.scrollTop === 0"


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_chat_with_a_long_history_scrolls_inside_the_panel_and_keeps_the_composer_in_view(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    """CALL-44: Chat with a long history scrolls inside the panel and keeps the composer in view."""
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


def test_chat_sends_the_typed_message_when_enter_is_pressed(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    _, guest, _ = open_call(open_person, base_url, page_errors, DESKTOP)
    guest.click("#chatBtn")

    guest.fill("#chatInput", "sent with enter")
    guest.press("#chatInput", "Enter")

    expect(guest.locator("#chatMessages .chat-msg.self")).to_have_text("sent with enter")
    expect(guest.locator("#chatInput")).to_have_value("")


def test_chat_panel_closes_from_its_close_button_found_by_name(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    _, guest, _ = open_call(open_person, base_url, page_errors, DESKTOP)
    guest.click("#chatBtn")
    expect(guest.locator("#chatPanel")).to_have_class("chat-panel visible")

    guest.get_by_role("button", name=text("de", "chat.closeButton")).click()

    expect(guest.locator("#chatPanel")).not_to_have_class("chat-panel visible")


def _three_people(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> tuple[Page, Page, Page, str]:
    host, link = open_host(open_person, base_url, page_errors, DESKTOP, RECORD_SOCKET_FRAMES)
    scripts = (RECORD_PEER_CONNECTIONS, RECORD_SOCKET_FRAMES)
    guest = join(open_person, link, DESKTOP, scripts, page_errors)
    third = join(open_person, link, DESKTOP, scripts, page_errors)
    for person in (host, guest, third):
        wait_for_peer_count(person, 2)
    return host, guest, third, link


def test_a_line_reaches_both_other_people_under_the_senders_name_and_no_socket_carries_its_text(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    """CALL-71: A line reaches every other person over the call's own connections, so no signaling socket carries it."""
    host, guest, third, _ = _three_people(open_person, base_url, page_errors)
    line = "only between the browsers"

    say_in_chat(guest, line)

    for reader in (host, third):
        reader.click("#chatBtn")
        expect(reader.locator(HEARD_LINE)).to_contain_text(line)
        expect(reader.locator(f"{HEARD_LINE} .chat-sender")).not_to_be_empty()
    expect(guest.locator(SENT_LINE)).to_have_text(line)
    expect(guest.locator(f"{SENT_LINE}.pending")).to_have_count(0)
    for person in (host, guest, third):
        assert line not in person.evaluate(SOCKET_FRAMES)


def test_a_person_who_joins_later_reads_none_of_the_earlier_lines(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    """CALL-71: A line reaches the people the sender's page shows when it is sent, and a later joiner reads none."""
    host, guest, link = open_call(open_person, base_url, page_errors, DESKTOP)
    say_in_chat(host, "before the third")
    expect(guest.locator(HEARD_LINE)).to_have_count(1)

    late = join(open_person, link, DESKTOP, (RECORD_PEER_CONNECTIONS,), page_errors)
    wait_for_peer_count(late, 2)
    say_in_chat(guest, "after the third")

    late.click("#chatBtn")
    expect(late.locator(HEARD_LINE)).to_have_text([re.compile("after the third$")])


def test_the_composer_sends_a_line_at_the_limit_and_says_why_one_over_it_is_not_sent(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    """CALL-71: A line holds 1 to 600 characters; the composer says why a longer draft is not sent."""
    _, guest, _ = open_call(open_person, base_url, page_errors, DESKTOP)
    guest.click("#chatBtn")
    at_the_limit = SMILEY * LINE_LIMIT
    over_the_limit = SMILEY * (LINE_LIMIT + 1)

    guest.fill("#chatInput", at_the_limit)
    expect(guest.locator("#chatDraftProblem")).to_have_count(0)
    guest.click("#chatSend")
    expect(guest.locator(SENT_LINE)).to_have_text(at_the_limit)

    guest.fill("#chatInput", over_the_limit)
    guest.click("#chatSend")

    expect(guest.locator("#chatDraftProblem")).to_have_text(text("de", "chat.lineTooLong", max=str(LINE_LIMIT)))
    expect(guest.locator("#chatInput")).to_have_value(over_the_limit)
    expect(guest.locator(SENT_LINE)).to_have_count(1)
