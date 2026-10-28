import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest
from agent_runtime_client.wire import RejectionCode
from playwright.sync_api import Page, Route, expect

from hopin.game.vocabulary import BROWSER_MODULE, GAME_LINE_MAX_CHARACTERS
from tests.e2e.support.calls import (
    PREJOIN_START,
    add_init_scripts,
    call_link_of,
    open_call,
    open_host,
    open_more,
    open_prejoin,
    record_page_errors,
    wait_until_the_prejoin_media_answered,
)
from tests.e2e.support.media import ICE_SERVERS_URL_PATTERN
from tests.e2e.support.page_assertions import LEGAL_LINKS, VISIBLE_MORE_ENTRIES
from tests.e2e.support.people import DESKTOP, FORM_FACTORS
from tests.game.support.fake_agent_service import Holds, Rejects, Succeeds

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.server import RunningServer
    from tests.game.support.fake_agent_service import FakeAgentService

pytestmark = pytest.mark.e2e

START_QUIZ = "#gameStartBtn"
GAME_MASTER_LINES = "#chatMessages .game-master:not(.typing)"
TYPING = "#chatMessages .game-master.typing"
TOASTS = "#toasts"
THINKING = re.compile(r"\bthinking\b")
PRIVACY_NOTICE = "https://hallucinai.de/datenschutz#hopin-spielleiter"
RECORD_WHETHER_THE_GAME_IS_IN_AT_EACH_CONNECT = """
window.gameInAtConnect = [];
const PageWebSocket = window.WebSocket;
window.WebSocket = class extends PageWebSocket {
    constructor(...socketArguments) {
        window.gameInAtConnect.push(document.getElementById("gameStartBtn") !== null);
        super(...socketArguments);
    }
};
"""


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


def test_the_page_connects_only_once_the_game_is_installed_even_when_its_module_arrives_last(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str]
) -> None:
    host = record_page_errors(open_person(**DESKTOP), page_errors)
    add_init_scripts(host, (RECORD_WHETHER_THE_GAME_IS_IN_AT_EACH_CONNECT,))
    held_modules: list[Route] = []
    host.route(f"**{BROWSER_MODULE}", lambda route: held_modules.append(route))
    open_prejoin(host, e2e_server.base_url)
    wait_until_the_prejoin_media_answered(host)

    with host.expect_response(ICE_SERVERS_URL_PATTERN):
        host.click(PREJOIN_START)
    [held_module] = held_modules
    held_module.continue_()

    call_link_of(host)
    assert host.evaluate("() => window.gameInAtConnect") == [True]


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_more_holds_start_quiz_under_lock_call_for_the_host_and_starts_on_it_for_the_guest(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, form_factor)

    open_more(host)
    menu = open_more(guest)

    assert host.evaluate(VISIBLE_MORE_ENTRIES) == ["Lock call", "Start quiz", "separator", *LEGAL_LINKS]
    assert guest.evaluate(VISIBLE_MORE_ENTRIES) == ["Start quiz", "separator", *LEGAL_LINKS]
    expect(menu.locator(START_QUIZ)).to_be_focused()


def test_more_says_under_start_quiz_what_goes_to_the_ai_service(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str]
) -> None:
    host, _ = open_host(open_person, e2e_server.base_url, page_errors, DESKTOP)

    menu = open_more(host)

    note = menu.locator(f"{START_QUIZ} + .quiet-note")
    expect(note).to_have_text("Quiz questions come from an AI service. Chat does not go there. Datenschutz")
    expect(note.get_by_role("link", name="Datenschutz")).to_have_attribute("href", PRIVACY_NOTICE)


def test_the_hosts_start_puts_one_question_marked_ai_in_both_chats(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], agent_service: FakeAgentService
) -> None:
    host, guest = call_of_two(open_person, e2e_server.base_url, page_errors)
    agent_service.answer_next(Succeeds("Welcher Fluss fließt durch Lissabon?"))

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
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], agent_service: FakeAgentService
) -> None:
    host, guest = call_of_two(open_person, e2e_server.base_url, page_errors)
    held = Holds()
    agent_service.answer_next(held)
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
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], agent_service: FakeAgentService
) -> None:
    host, guest = call_of_two(open_person, e2e_server.base_url, page_errors)
    agent_service.answer_next(Rejects(HTTPStatus.FORBIDDEN, RejectionCode.NOT_ENABLED))

    start_quiz(host)

    expect(host.locator(TOASTS)).to_have_text("Your account has no AI access.")
    expect_idle(host)
    open_more(guest)
    expect_idle(guest)
    expect(guest.locator(TOASTS)).to_be_empty()


def test_a_busy_game_master_names_the_seconds_the_service_asked_for(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], agent_service: FakeAgentService
) -> None:
    host, _ = call_of_two(open_person, e2e_server.base_url, page_errors)
    agent_service.answer_next(Rejects(HTTPStatus.TOO_MANY_REQUESTS, RejectionCode.BUSY, retry_after="17"))

    start_quiz(host)

    expect(host.locator(TOASTS)).to_have_text("The game master is busy. Try again in 17 s.")
    expect_idle(host)


def test_a_long_answer_is_cut_and_markup_shows_as_text(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], agent_service: FakeAgentService
) -> None:
    host, _ = call_of_two(open_person, e2e_server.base_url, page_errors)
    markup = "<b>fett</b><img src=x onerror=alert(1)>"
    agent_service.answer_next(Succeeds(markup + "x" * 2000))

    start_quiz(host)
    open_chat(host)

    line = host.locator(GAME_MASTER_LINES)
    expect(line).to_contain_text(markup)
    expect(line.locator("b, img")).to_have_count(0)
    question = line.evaluate("(element) => element.lastChild.textContent")
    assert len(question) == GAME_LINE_MAX_CHARACTERS
    assert question.endswith("…")


def test_sign_in_again_returns_to_this_call_through_the_sign_in(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str]
) -> None:
    host, _ = call_of_two(open_person, e2e_server.base_url, page_errors, with_refresh_token=False)
    call_path = host.evaluate("() => location.pathname")
    sign_in_requests: list[str] = []
    host.route(
        "**/auth/start?**",
        lambda route: sign_in_requests.append(route.request.url) or route.fulfill(status=200, body="sign-in"),
    )

    start_quiz(host)
    expect(host.locator(TOASTS)).to_contain_text("Sign in again to use the game master.")
    host.locator(TOASTS).get_by_role("button", name="Sign in").click()

    expect(host).to_have_url(f"{e2e_server.base_url}/auth/start?next={call_path.replace('/', '%2F')}")
    assert sign_in_requests == [f"{e2e_server.base_url}/auth/start?next={call_path.replace('/', '%2F')}"]
