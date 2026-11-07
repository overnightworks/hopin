from typing import TYPE_CHECKING

import pytest
from agent_runtime_client.turn import RejectionCode
from playwright.sync_api import Page, expect

from hopin.config.constants import SignalType
from hopin.game.quiz import QUIZ_ROUNDS
from tests.e2e.game.game_page import (
    CARD,
    end_quiz,
    open_chat,
    relaying_frames,
    start_quiz,
)
from tests.e2e.support.calls import join, open_call, press_start_call
from tests.e2e.support.mesh_pair import ConnectedPair, pair_connector, wait_for_status
from tests.e2e.support.people import DESKTOP
from tests.game.support.fake_agent_backend import Holds, Rejects, Succeeds

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from tests.e2e.support.server import RunningServer
    from tests.e2e.support.signaling import FrameInjector
    from tests.game.support.fake_agent_backend import FakeAgentBackend

pytestmark = pytest.mark.e2e

AGENT_TILE = "#container-agent"
AGENT_THINKING = "Game master, AI, thinking"
AGENT_WAITING = "Game master, AI, waiting for answers"
STAGE = "#stage"
CALL_PLACES = 6
REVEAL = "Reveal"
IN_CALL = "In call"
NO_AI_ACCESS = "Your account has no AI access."


@pytest.fixture
def game_pair(open_person: Callable[..., Page], e2e_server: RunningServer) -> Iterator[ConnectedPair]:
    """Two people in a call on the game's server, their signaling sockets cuttable."""
    for connect in pair_connector(open_person, e2e_server.base_url):
        yield connect()


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


def test_a_seat_that_resumes_within_the_window_shows_one_tile_in_the_current_state(
    game_pair: ConnectedPair, agent_backend: FakeAgentBackend
) -> None:
    """GM-35: A seat that resumes within the window shows one tile in the current state."""
    agent_backend.answer_next(Succeeds("Erste?"))
    start_quiz(game_pair.host)
    expect_agent_tile(game_pair.guest, AGENT_WAITING)

    game_pair.cut(game_pair.guest)
    game_pair.resume(game_pair.guest)

    expect_agent_tile(game_pair.guest, AGENT_WAITING)
    expect(game_pair.guest.locator(AGENT_TILE)).to_have_count(1)
    assert SignalType.RESUME in game_pair.guest_tap.types(game_pair.guest_tap.upstream)


def test_a_seat_whose_resume_is_refused_joins_afresh_and_the_phase_it_is_greeted_with_restores_the_tile(
    game_pair: ConnectedPair, agent_backend: FakeAgentBackend
) -> None:
    """GM-35: A seat whose resume is refused joins afresh and the phase it is greeted with restores the tile."""
    agent_backend.answer_next(Succeeds("Erste?"))
    start_quiz(game_pair.host)
    expect_agent_tile(game_pair.guest, AGENT_WAITING)
    game_pair.guest_tap.corrupt_resume_token = True

    game_pair.cut(game_pair.guest)
    game_pair.reconnect(game_pair.guest)

    wait_for_status(game_pair.guest, IN_CALL)
    sent = game_pair.guest_tap.types(game_pair.guest_tap.upstream)
    assert (sent.count(SignalType.JOIN), sent.count(SignalType.RESUME)) == (2, 1)
    expect_agent_tile(game_pair.guest, AGENT_WAITING)
    expect(game_pair.guest.locator(AGENT_TILE)).to_have_count(1)


def test_the_tile_goes_for_everyone_when_the_fifth_round_ends_the_quiz(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], agent_backend: FakeAgentBackend
) -> None:
    """GM-35: The tile goes for everyone when the fifth round ends the quiz."""
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)
    lines = [text for number in range(1, QUIZ_ROUNDS + 1) for text in (f"Frage {number}?", f"Antwort {number}.")]
    final_score_run = Holds(then=Succeeds("Ihr wart großartig."))
    agent_backend.answer_next(*map(Succeeds, lines), final_score_run)
    start_quiz(guest)
    for person in (host, guest):
        open_chat(person)

    for number in range(1, QUIZ_ROUNDS + 1):
        for person in (host, guest):
            expect(person.locator(CARD).locator(".qcard-tag")).to_have_text(f"Question {number} of {QUIZ_ROUNDS}")
        guest.locator(CARD).get_by_role("button", name=REVEAL).click()

    for person in (host, guest):
        expect_agent_tile(person, AGENT_THINKING)
    final_score_run.release.set()
    for person in (host, guest):
        expect(person.locator(AGENT_TILE)).to_have_count(0)


def test_a_run_refused_at_the_opening_takes_the_tile_back_for_everyone(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], agent_backend: FakeAgentBackend
) -> None:
    """GM-35: A run refused at the opening takes the tile back for everyone."""
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)
    agent_backend.answer_next(Rejects(RejectionCode.NOT_ENABLED))

    start_quiz(host)

    expect(host.locator("#toasts")).to_have_text(NO_AI_ACCESS)
    for person in (host, guest):
        expect(person.locator(AGENT_TILE)).to_have_count(0)


def test_a_run_refused_in_the_middle_of_the_quiz_takes_the_tile_back_for_everyone(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], agent_backend: FakeAgentBackend
) -> None:
    """GM-35: A run refused in the middle of the quiz takes the tile back for everyone."""
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)
    agent_backend.answer_next(Succeeds("Erste?"), Rejects(RejectionCode.NOT_ENABLED))
    start_quiz(host)
    for person in (host, guest):
        open_chat(person)
        expect_agent_tile(person, AGENT_WAITING)

    host.locator(CARD).get_by_role("button", name=REVEAL).click()

    expect(host.locator("#toasts")).to_have_text(NO_AI_ACCESS)
    for person in (host, guest):
        expect(person.locator(AGENT_TILE)).to_have_count(0)


def test_the_tile_goes_for_the_others_when_the_starter_leaves(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], agent_backend: FakeAgentBackend
) -> None:
    """GM-35: The tile goes for the others when the starter leaves."""
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)
    agent_backend.answer_next(Succeeds("Erste?"))
    start_quiz(guest)
    expect_agent_tile(host, AGENT_WAITING)
    guest.keyboard.press("Escape")

    guest.click("#leaveBtn")

    expect(host.locator(AGENT_TILE)).to_have_count(0)


def test_a_new_call_after_the_end_of_one_with_a_running_quiz_shows_no_tile(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], agent_backend: FakeAgentBackend
) -> None:
    """GM-35: A new call after the end of one with a running quiz shows no tile."""
    relays: list[FrameInjector] = []
    host, guest, _ = open_call(relaying_frames(open_person, relays), e2e_server.base_url, page_errors, DESKTOP)
    agent_backend.answer_next(Succeeds("Erste?"))
    start_quiz(host)
    expect_agent_tile(guest, AGENT_WAITING)

    relays[0].send({"type": SignalType.END_ROOM})
    expect(guest.locator("[data-screen=end]")).to_be_visible()
    expect(guest.locator(AGENT_TILE)).to_have_count(0)
    guest.get_by_role("button", name="Back to start").click()
    expect(guest).to_have_url(f"{e2e_server.base_url}/")
    expect(guest.locator("[data-screen=home]")).to_be_visible()
    press_start_call(guest, e2e_server.base_url)

    expect(guest.locator("#stage")).to_be_visible()
    expect(guest.locator(AGENT_TILE)).to_have_count(0)
