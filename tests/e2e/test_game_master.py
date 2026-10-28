import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest
from agent_runtime_client.wire import RejectionCode
from playwright.sync_api import Page, expect

from hopin.config.constants import GAME_LINE_MAX_CHARACTERS
from tests.e2e.support.calls import open_call, open_host, open_more
from tests.e2e.support.people import DESKTOP
from tests.support.fake_agent_service import Holds, Rejects, Succeeds

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

START_QUIZ = "#gameStartBtn"
GAME_MASTER_LINES = "#chatMessages .game-master:not(.typing)"
TYPING = "#chatMessages .game-master.typing"
TOASTS = "#toasts"
THINKING = re.compile(r"\bthinking\b")
PRIVACY_NOTICE = "https://hallucinai.de/datenschutz#hopin-spielleiter"


def start_quiz(person: Page) -> None:
    open_more(person)
    person.click(START_QUIZ)


def open_chat(person: Page) -> None:
    if person.locator("#moreMenu").is_visible():
        person.keyboard.press("Escape")
    person.click("#chatBtn")
    expect(person.locator("#chatPanel")).to_be_visible()


def expect_idle(person: Page) -> None:
    item = person.locator(START_QUIZ)
    expect(item).to_be_enabled()
    expect(item).not_to_have_class(THINKING)
    expect(person.locator(TYPING)).to_have_count(0)


def call_of_two(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], **options: Any
) -> tuple[Page, Page]:
    host, guest, _ = open_call(open_person, base_url, page_errors, {**DESKTOP, **options})
    return host, guest


def test_more_says_under_start_quiz_what_goes_to_the_ai_service(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host, _ = open_host(open_person, base_url, page_errors, DESKTOP)

    menu = open_more(host)

    note = menu.locator(f"{START_QUIZ} + .quiet-note")
    expect(note).to_have_text("Quiz questions come from an AI service. Chat does not go there. Datenschutz")
    expect(note.get_by_role("link", name="Datenschutz")).to_have_attribute("href", PRIVACY_NOTICE)


def test_the_hosts_start_puts_one_question_marked_ai_in_both_chats(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], e2e_server: RunningServer
) -> None:
    host, guest = call_of_two(open_person, base_url, page_errors)
    e2e_server.agent_service.answer_next(Succeeds("Welcher Fluss fließt durch Lissabon?"))

    start_quiz(host)

    for person in (host, guest):
        open_chat(person)
        line = person.locator(GAME_MASTER_LINES)
        expect(line).to_have_count(1)
        expect(line.locator(".chat-sender")).to_have_text("Game masterAI")
        expect(line.locator(".ai-badge")).to_be_visible()
        expect(line).to_contain_text("Welcher Fluss fließt durch Lissabon?")
        expect_idle(person)


def test_the_guest_sees_the_game_master_thinking_while_the_run_is_in_flight(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], e2e_server: RunningServer
) -> None:
    host, guest = call_of_two(open_person, base_url, page_errors)
    held = Holds()
    e2e_server.agent_service.answer_next(held)
    open_chat(guest)

    start_quiz(host)
    open_more(guest)

    expect(guest.locator(START_QUIZ)).to_be_disabled()
    expect(guest.locator(START_QUIZ)).to_contain_text("Game master is thinking…")
    expect(guest.locator(TYPING)).to_be_visible()
    held.release.set()
    expect(guest.locator(GAME_MASTER_LINES)).to_have_count(1)
    expect_idle(guest)


def test_an_account_without_ai_access_gets_the_toast_alone_and_the_other_returns_to_idle(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], e2e_server: RunningServer
) -> None:
    host, guest = call_of_two(open_person, base_url, page_errors)
    e2e_server.agent_service.answer_next(Rejects(HTTPStatus.FORBIDDEN, RejectionCode.NOT_ENABLED))

    start_quiz(host)

    expect(host.locator(TOASTS)).to_have_text("Your account has no AI access.")
    expect_idle(host)
    open_more(guest)
    expect_idle(guest)
    expect(guest.locator(TOASTS)).to_be_empty()


def test_a_busy_game_master_names_the_seconds_the_service_asked_for(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], e2e_server: RunningServer
) -> None:
    host, _ = call_of_two(open_person, base_url, page_errors)
    e2e_server.agent_service.answer_next(Rejects(HTTPStatus.TOO_MANY_REQUESTS, RejectionCode.BUSY, retry_after="17"))

    start_quiz(host)

    expect(host.locator(TOASTS)).to_have_text("The game master is busy. Try again in 17 s.")
    expect_idle(host)


def test_a_long_answer_is_cut_and_markup_shows_as_text(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], e2e_server: RunningServer
) -> None:
    host, _ = call_of_two(open_person, base_url, page_errors)
    markup = "<b>fett</b><img src=x onerror=alert(1)>"
    e2e_server.agent_service.answer_next(Succeeds(markup + "x" * 2000))

    start_quiz(host)
    open_chat(host)

    line = host.locator(GAME_MASTER_LINES)
    expect(line).to_contain_text(markup)
    expect(line.locator("b, img")).to_have_count(0)
    question = line.evaluate("(element) => element.lastChild.textContent")
    assert len(question) == GAME_LINE_MAX_CHARACTERS
    assert question.endswith("…")


def test_sign_in_again_returns_to_this_call_through_the_sign_in(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host, _ = call_of_two(open_person, base_url, page_errors, with_refresh_token=False)
    call_path = host.evaluate("() => location.pathname")
    sign_in_requests: list[str] = []
    host.route(
        "**/auth/start?**",
        lambda route: sign_in_requests.append(route.request.url) or route.fulfill(status=200, body="sign-in"),
    )

    start_quiz(host)
    expect(host.locator(TOASTS)).to_contain_text("Sign in again to use the game master.")
    host.locator(TOASTS).get_by_role("button", name="Sign in").click()

    expect(host).to_have_url(f"{base_url}/auth/start?next={call_path.replace('/', '%2F')}")
    assert sign_in_requests == [f"{base_url}/auth/start?next={call_path.replace('/', '%2F')}"]
