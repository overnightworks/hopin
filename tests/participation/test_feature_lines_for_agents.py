"""A person's line for the agents of its call, at participation's real feature and handler: the page sends it only
while it shows a joined agent, and the server holds it in the windows of the agents joined when it arrives."""

import json

import pytest

from hopin.config.constants import LINE_LIMITS
from hopin.participation.chat_window import LineKind
from hopin.participation.vocabulary import ParticipationSignal
from tests.participation.support.people import person_named, room_of
from tests.signaling.support.browsers import host_and_guest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

SMILEY = "\N{GRINNING FACE}"


def line_for_agents(text):
    return {"type": ParticipationSignal.LINE_FOR_AGENTS.value, "text": text}


def texts_of(participation, browser):
    window = participation.read_window(person_named(browser.name), room_of(browser))
    return [line.text for line in window.lines]


async def dial(participation, browser):
    return await participation.dial_in(person_named(browser.name), room_of(browser), "Claude")


class TestWhereTheLineGoes:
    async def test_a_line_is_held_for_the_joined_agent_under_the_name_the_call_shows_and_for_nobody_else(
        self, link, participation
    ):
        host, guest = await host_and_guest(link)
        await dial(participation, guest)
        heard_before = (len(host.socket.sent), len(guest.socket.sent))

        await host.say(line_for_agents("hello"))

        window = participation.read_window(person_named("guest"), room_of(host))
        assert [(line.kind, line.name, line.text) for line in window.lines] == [(LineKind.PERSON, "host", "hello")]
        sent_since = host.socket.sent[heard_before[0] :] + guest.socket.sent[heard_before[1] :]
        assert "hello" not in json.dumps(sent_since)

    async def test_a_line_for_an_agent_that_left_the_server_is_held_for_no_agent(self, link, participation):
        host, guest = await host_and_guest(link)
        await dial(participation, guest)
        await participation.leave(person_named("guest"), room_of(host))
        heard_before = (len(host.socket.sent), len(guest.socket.sent))

        await host.say(line_for_agents("still shown on the page"))

        assert participation.read_window(person_named("guest"), room_of(host)) is None
        assert (len(host.socket.sent), len(guest.socket.sent)) == heard_before

    async def test_a_line_from_a_modified_page_while_no_agent_is_joined_changes_nothing_and_tells_no_seat(
        self, link, participation, window_changes
    ):
        host, guest = await host_and_guest(link)
        heard_before = (len(host.socket.sent), len(guest.socket.sent))

        await host.say(line_for_agents("nobody reads this"))

        assert window_changes == []
        assert (len(host.socket.sent), len(guest.socket.sent)) == heard_before


class TestWhatTheServerDrops:
    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("x" * (LINE_LIMITS.max_characters + 1), id="over_the_length"),
            pytest.param(SMILEY * (LINE_LIMITS.max_characters + 1), id="over_the_length_in_code_points"),
            pytest.param("two\nlines", id="a_control_character"),
            pytest.param("   ", id="no_line_after_trimming"),
        ],
    )
    async def test_a_line_beyond_the_limits_leaves_the_window_as_it_was(self, link, participation, text):
        host, guest = await host_and_guest(link)
        await dial(participation, guest)

        await host.say(line_for_agents(text))

        assert texts_of(participation, guest) == []

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("x" * LINE_LIMITS.max_characters, id="at_the_length"),
            pytest.param(SMILEY * LINE_LIMITS.max_characters, id="at_the_length_in_code_points"),
        ],
    )
    async def test_a_line_at_the_length_is_held_whole(self, link, participation, text):
        host, guest = await host_and_guest(link)
        await dial(participation, guest)

        await host.say(line_for_agents(text))

        assert texts_of(participation, guest) == [text]

    async def test_lines_beyond_the_burst_are_dropped_until_the_budget_refills(self, link, participation, scheduler):
        host, guest = await host_and_guest(link)
        await dial(participation, guest)

        for number in range(LINE_LIMITS.burst_lines + 1):
            await host.say(line_for_agents(f"line {number}"))
        held_at_once = texts_of(participation, guest)
        await scheduler.advance(1 / LINE_LIMITS.lines_per_second)
        await host.say(line_for_agents("after the refill"))

        assert held_at_once == [f"line {number}" for number in range(LINE_LIMITS.burst_lines)]
        assert texts_of(participation, guest)[-1] == "after the refill"

    async def test_a_budget_belongs_to_its_seat(self, link, participation):
        host, guest = await host_and_guest(link)
        await dial(participation, host)
        for number in range(LINE_LIMITS.burst_lines):
            await guest.say(line_for_agents(f"guest {number}"))

        await host.say(line_for_agents("host"))
        await guest.say(line_for_agents("one too many"))

        assert texts_of(participation, host)[-1] == "host"
        assert "one too many" not in texts_of(participation, host)

    async def test_a_line_without_text_is_answered_invalid_message(self, link, participation):
        host, _guest = await host_and_guest(link)

        await host.say({"type": ParticipationSignal.LINE_FOR_AGENTS.value, "text": 5})

        assert host.socket.sent[-1]["reason"] == "invalid_message"
