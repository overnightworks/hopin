from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import Page, expect

from hopin.game.vocabulary import GameSignal
from hopin.words import text
from tests.e2e.game.game_page import (
    CARD,
    GAME_MASTER_NAME_GAP,
    START_QUIZ,
    call_of_two,
    open_chat,
    relaying_frames,
    start_quiz,
)
from tests.e2e.support.calls import open_more
from tests.e2e.support.page_assertions import TOASTS
from tests.game.support.fake_agent_backend import Fails, Holds

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.server import RunningServer
    from tests.e2e.support.signaling import FrameInjector
    from tests.game.support.fake_agent_backend import FakeAgentBackend

pytestmark = pytest.mark.e2e


def test_a_second_start_while_the_game_master_runs_is_refused_to_its_sender_alone(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], agent_backend: FakeAgentBackend
) -> None:
    """GM-10: A second start is refused to the person who tapped it alone."""
    relays: list[FrameInjector] = []
    host, guest = call_of_two(relaying_frames(open_person, relays), e2e_server.base_url, page_errors)
    _, guest_frames = relays
    held = Holds()
    agent_backend.answer_next(held)
    start_quiz(host)

    guest_frames.send({"type": GameSignal.GAME_START})

    expect(guest.locator(TOASTS)).to_contain_text(text("de", "game.alreadyAnsweringToast", **GAME_MASTER_NAME_GAP))
    expect(host.locator(TOASTS)).to_be_empty()
    held.release.set()
    open_chat(host)
    expect(host.locator(CARD)).to_be_visible()


def test_a_failed_game_master_says_it_is_unavailable_to_the_starter_alone(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], agent_backend: FakeAgentBackend
) -> None:
    """GM-13: On any other failure the starter sees that the game master is unavailable."""
    host, guest = call_of_two(open_person, e2e_server.base_url, page_errors)
    agent_backend.answer_next(Fails())

    start_quiz(host)

    expect(host.locator(TOASTS)).to_have_text(text("de", "game.unavailableToast", **GAME_MASTER_NAME_GAP))
    expect(host.locator(START_QUIZ)).to_be_enabled()
    open_more(guest)
    expect(guest.locator(TOASTS)).to_be_empty()
