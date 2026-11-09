import re
from typing import TYPE_CHECKING, Any

import pytest
from agent_runtime_client.member import STANDING_URI
from playwright.sync_api import expect

from hopin.agent.tools import SAY
from tests.e2e.support.calls import (
    call_link_of,
    join_by_link,
    open_call,
    open_more,
    press_start_call,
    record_page_errors,
    say_in_chat,
)
from tests.e2e.support.dialed_agent import OWNER_USERNAME
from tests.e2e.support.people import FORM_FACTORS
from tests.support.dialed_call import window_uri

if TYPE_CHECKING:
    from collections.abc import Callable

    from playwright.sync_api import Page

    from tests.e2e.support.dialed_agent import OwnAccountAgent, OwnAgent
    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

OFFER = "#agentOffers button"
WAKE_SECONDS = 20
CHAT_LINE = "#chatMessages .chat-msg"


def room_of(link: str) -> str:
    found = re.search(r"/c/(\w+)", link)
    assert found is not None
    return found.group(1)


def lines_read(agent: Any, room: str) -> list[tuple[Any, ...]]:
    window = agent.read(window_uri(room))
    return [(line["seq"], line["kind"], line["name"], line["text"], line["own"]) for line in window["entries"]]


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_call_54_an_agent_with_an_account_of_its_own_is_woken_by_a_line_and_reads_it_with_its_own_marked(
    open_person: Callable[..., Page],
    member_of: Callable[[Page], Any],
    e2e_server: RunningServer,
    page_errors: list[str],
    grant_adders: Callable[[str], None],
    own_account_agent: OwnAccountAgent,
    form_factor: dict[str, Any],
) -> None:
    """CALL-54: A joined agent is woken by a line said after its join and reads it in its window, its own marked."""
    host = record_page_errors(open_person(**form_factor), page_errors)
    press_start_call(host, e2e_server.base_url)
    link = call_link_of(host)
    room = room_of(link)
    owner = record_page_errors(open_person(username=OWNER_USERNAME, **form_factor), page_errors)
    join_by_link(owner, link)
    member = record_page_errors(open_person(**form_factor), page_errors)
    grant_adders(member_of(member).account.id)
    join_by_link(member, link)
    own_account_agent.listen()
    say_in_chat(host, "before the join")
    member.click("#chatBtn")
    expect(member.locator(CHAT_LINE)).to_have_count(1)
    open_more(member)
    offer = member.locator(OFFER, has_text=own_account_agent.name)
    offer.click()
    own_account_agent.accept(own_account_agent.invitation_within(WAKE_SECONDS))
    own_account_agent.listen([STANDING_URI, window_uri(room)])

    say_in_chat(host, "hello agent")

    assert own_account_agent.woken_within(WAKE_SECONDS)
    host_name = member_of(host).account.name
    assert lines_read(own_account_agent, room) == [(1, "person", host_name, "hello agent", False)]
    own_account_agent.forget_wakes()
    own_account_agent.call(SAY, {"room": room, "text": "hello people"})
    assert own_account_agent.woken_within(WAKE_SECONDS)
    assert lines_read(own_account_agent, room)[1:] == [(2, "agent", own_account_agent.name, "hello people", True)]


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_call_54_a_persons_own_agent_is_woken_by_a_line_and_reads_it(
    open_person: Callable[..., Page],
    member_of: Callable[[Page], Any],
    e2e_server: RunningServer,
    page_errors: list[str],
    own_agent_of: Callable[[str], OwnAgent],
    form_factor: dict[str, Any],
) -> None:
    """CALL-54: A person's own agent listens to its window's URI, is woken by a line and reads it."""
    host, guest, link = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    room = room_of(link)
    agent = own_agent_of(member_of(host).account.id)
    agent.dial_in(room, "Claude")
    agent.listen([window_uri(room)])

    say_in_chat(guest, "hello agent")

    assert agent.woken_within(WAKE_SECONDS)
    assert lines_read(agent, room) == [(1, "person", member_of(guest).account.name, "hello agent", False)]
