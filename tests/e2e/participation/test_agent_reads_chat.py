import re
from typing import TYPE_CHECKING, Any

import pytest
from agents.member import STANDING_URI
from playwright.sync_api import expect

from hopin.agent.tools import SAY
from tests.e2e.support.calls import (
    open_call,
    say_in_chat,
)
from tests.e2e.support.dialed_agent import room_of_link
from tests.e2e.support.people import FORM_FACTORS
from tests.support.dialed_call import window_uri

if TYPE_CHECKING:
    from collections.abc import Callable

    from playwright.sync_api import Page

    from tests.e2e.support.dialed_agent import OwnAgent
    from tests.e2e.support.mesh_pair import ConnectedPair
    from tests.e2e.support.participation_scene import Scene
    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

WAKE_SECONDS = 20
CHAT_LINE = "#chatMessages .chat-msg"
READERS_NOTE = "#chatReaders"
HEARD_LINE = "#chatMessages .chat-msg.other"


def lines_read(agent: Any, room: str) -> list[tuple[Any, ...]]:
    window = agent.read(window_uri(room))
    return [(line["seq"], line["kind"], line["name"], line["text"], line["own"]) for line in window["entries"]]


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_call_54_an_agent_with_an_account_of_its_own_is_woken_by_a_line_and_reads_it_with_its_own_marked(
    scene_of: Callable[[dict[str, Any]], Scene],
    member_of: Callable[[Page], Any],
    form_factor: dict[str, Any],
) -> None:
    """CALL-54: A joined agent is woken by a line said after its join and reads it in its window, its own marked."""
    scene = scene_of(form_factor)
    agent = scene.agent
    room = room_of_link(scene.link)
    say_in_chat(scene.host, "before the join")
    scene.member.click("#chatBtn")
    expect(scene.member.locator(CHAT_LINE)).to_have_count(1)
    scene.member.click("#chatClose")
    scene.member_adds_and_the_agent_accepts()
    agent.listen([STANDING_URI, window_uri(room)])
    expect(scene.host.locator(READERS_NOTE)).to_contain_text(agent.name)

    say_in_chat(scene.host, "hello agent")

    assert agent.woken_within(WAKE_SECONDS)
    host_name = member_of(scene.host).account.username
    assert lines_read(agent, room) == [(1, "person", host_name, "hello agent", False)]
    agent.forget_wakes()
    agent.call(SAY, {"room": room, "text": "hello people"})
    assert agent.woken_within(WAKE_SECONDS)
    assert lines_read(agent, room)[1:] == [(2, "agent", agent.name, "hello people", True)]


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
    room = room_of_link(link)
    agent = own_agent_of(member_of(host).account.id)
    agent.dial_in(room, "Claude")
    agent.listen([window_uri(room)])
    guest.click("#chatBtn")
    expect(guest.locator(READERS_NOTE)).to_contain_text("Claude")

    say_in_chat(guest, "hello agent")

    assert agent.woken_within(WAKE_SECONDS)
    assert lines_read(agent, room) == [(1, "person", member_of(guest).account.username, "hello agent", False)]


def test_call_71_a_line_said_in_a_cut_reaches_people_at_once_and_the_agent_once_after_the_resume(
    connected_pair: ConnectedPair,
    member_of: Callable[[Page], Any],
    own_agent_of: Callable[[str], OwnAgent],
) -> None:
    """CALL-71: People read a line at once over the channel; the agent's window gets it once, after the resume."""
    host, guest = connected_pair.host, connected_pair.guest
    room = room_of_link(connected_pair.invite_link)
    agent = own_agent_of(member_of(host).account.id)
    agent.dial_in(room, "Claude")
    agent.listen([window_uri(room)])
    guest.click("#chatBtn")
    expect(guest.locator(READERS_NOTE)).to_contain_text("Claude")

    with connected_pair.socket_down(guest):
        say_in_chat(guest, "said in the cut")
        expect(host.locator(HEARD_LINE)).to_have_text([re.compile("said in the cut$")])
        assert lines_read(agent, room) == []

    assert agent.woken_within(WAKE_SECONDS)
    assert lines_read(agent, room) == [(1, "person", member_of(guest).account.username, "said in the cut", False)]
