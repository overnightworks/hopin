from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.game.game_page import CARD, end_quiz, open_chat, start_quiz
from tests.e2e.support.calls import join, open_call
from tests.e2e.support.people import DESKTOP
from tests.game.support.fake_agent_backend import Holds, Succeeds

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.server import RunningServer
    from tests.game.support.fake_agent_backend import FakeAgentBackend

pytestmark = pytest.mark.e2e

AGENT_TILE = "#container-agent"
AGENT_THINKING = "Game master, AI, thinking"
AGENT_WAITING = "Game master, AI, waiting for answers"
STAGE = "#stage"
CALL_PLACES = 6


def expect_agent_tile(person: Page, label: str) -> None:
    tile = person.locator(AGENT_TILE)
    expect(tile).to_have_attribute("aria-label", label)
    expect(tile.locator(".ai-badge")).to_be_visible()


def test_the_quiz_masters_tile_shows_thinking_then_waiting_and_goes_when_the_starter_ends_the_quiz(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], agent_backend: FakeAgentBackend
) -> None:
    """GM-35: The quiz masters tile shows thinking then waiting and goes when the starter ends the quiz."""
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)
    held = Holds()
    agent_backend.answer_next(held)
    for person in (host, guest):
        expect(person.locator(AGENT_TILE)).to_have_count(0)

    start_quiz(host)

    for person in (host, guest):
        expect_agent_tile(person, AGENT_THINKING)
    held.release.set()
    for person in (host, guest):
        expect_agent_tile(person, AGENT_WAITING)
    end_quiz(host)
    for person in (host, guest):
        expect(person.locator(AGENT_TILE)).to_have_count(0)


def test_a_page_that_joins_during_a_question_shows_the_waiting_tile(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], agent_backend: FakeAgentBackend
) -> None:
    """GM-35: A page that joins during a question shows the waiting tile."""
    host, _guest, invite_link = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)
    agent_backend.answer_next(Succeeds("Erste?"))
    start_quiz(host)
    open_chat(host)
    expect(host.locator(CARD)).to_be_visible()

    late = join(open_person, invite_link, DESKTOP, (), page_errors)

    expect_agent_tile(late, AGENT_WAITING)


def test_the_quiz_masters_tile_is_a_seventh_tile_beside_six_people_and_the_count_names_people_only(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], agent_backend: FakeAgentBackend
) -> None:
    """CALL-47: The quiz masters tile is a seventh tile beside six people and the count names people only."""
    host, _, invite_link = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)
    for _ in range(CALL_PLACES - 2):
        join(open_person, invite_link, DESKTOP, (), page_errors)
    agent_backend.answer_next(Succeeds("Erste?"))
    expect(host.locator("#participantCount .num")).to_have_text(str(CALL_PLACES))

    start_quiz(host)

    expect_agent_tile(host, AGENT_WAITING)
    expect(host.locator(STAGE)).to_have_attribute("data-count", str(CALL_PLACES + 1))
    expect(host.locator("#participantCount .num")).to_have_text(str(CALL_PLACES))
