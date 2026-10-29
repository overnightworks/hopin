import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest
from agent_runtime_client.wire import RejectionCode
from playwright.sync_api import Page, Route, expect

from hopin.config.constants import STATIC_PATH
from hopin.game.quiz import QUIZ_ROUNDS
from hopin.game.vocabulary import BROWSER_MODULE, GAME_ANSWER_MAX_CHARACTERS, GAME_LINE_MAX_CHARACTERS, GameSignal
from tests.e2e.support.calls import (
    PREJOIN_START,
    add_init_scripts,
    call_link_of,
    join,
    open_call,
    open_host,
    open_more,
    open_prejoin,
    press_start_call,
    record_page_errors,
    wait_until_the_prejoin_media_answered,
)
from tests.e2e.support.media import ICE_SERVERS_URL_PATTERN
from tests.e2e.support.page_assertions import LEGAL_LINKS, VISIBLE_MORE_ENTRIES, X_ICON_PATH, expect_toast
from tests.e2e.support.people import DESKTOP, FORM_FACTORS
from tests.game.support.fake_agent_service import Holds, Rejects, Succeeds
from tests.game.support.table_world import answers_in_reveal

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.server import RunningServer
    from tests.game.support.fake_agent_service import FakeAgentService
    from tests.support.signed_in import SeededMember

pytestmark = pytest.mark.e2e

START_QUIZ = "#gameStartBtn"
GAME_MASTER_LINES = "#chatMessages .game-master:not(.typing)"
TYPING = "#chatMessages .game-master.typing"
TOASTS = "#toasts"
CARD = "#chatPanel .qcard"
REVEAL = "Reveal"
SEND = "Send"
YOUR_ANSWER = "Your answer"
STATUS = ".qcard-status"
SENT = re.compile(r"\bsent\b")
MORE_PROGRESS = "#gameProgress"
THINKING = re.compile(r"\bthinking\b")
QUESTION_ONE = f"Question 1 of {QUIZ_ROUNDS}"
QUIZ_RUNNING = re.compile(rf"^Quiz running\s+{QUESTION_ONE}$")
END_QUIZ = re.compile(rf"^End quiz\s+{QUESTION_ONE}$")
QUIZ_ENDED = "Quiz ended."
SEND_FRAME_THROUGH_THE_PAGES_SIGNALING = (
    f"async (type) => (await import('{STATIC_PATH}/js/signaling.js')).send({{ type }})"
)
PRIVACY_NOTICE = "https://hallucinai.de/datenschutz#hopin-spielleiter"
BROWSER_STYLESHEET = BROWSER_MODULE.replace("game.js", "game.css")
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


def end_quiz(person: Page) -> None:
    open_more(person)
    person.click(START_QUIZ)


def leave_the_call(guest: Page) -> None:
    """A guest leaves at once; only the host's Leave asks first."""
    guest.click("#leaveBtn")


def send_frame(person: Page, signal_type: GameSignal) -> None:
    person.evaluate(SEND_FRAME_THROUGH_THE_PAGES_SIGNALING, signal_type.value)


def open_chat(person: Page) -> None:
    if person.locator("#moreMenu").is_visible():
        person.keyboard.press("Escape")
    person.click("#chatBtn")
    expect(person.locator("#chatPanel")).to_be_visible()


def close_chat(person: Page) -> None:
    person.click("#chatClose")
    expect(person.locator("#chatPanel")).to_be_hidden()


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


@pytest.mark.parametrize("unreachable_file", [BROWSER_MODULE, BROWSER_STYLESHEET])
def test_the_call_starts_without_the_game_and_says_so_when_a_file_of_it_does_not_load(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], unreachable_file: str
) -> None:
    host = record_page_errors(open_person(**DESKTOP), page_errors)
    host.route(f"**{unreachable_file}", lambda route: route.abort())

    press_start_call(host, e2e_server.base_url)

    expect(host.locator(TOASTS)).to_have_text("An add-on could not load.")
    assert call_link_of(host)
    menu = open_more(host)
    expect(menu.locator(START_QUIZ)).to_have_count(0)


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


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_more_says_under_start_quiz_what_goes_to_the_ai_service(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host, _ = open_host(open_person, e2e_server.base_url, page_errors, form_factor)

    menu = open_more(host)

    note = menu.locator(f"{START_QUIZ} + .quiet-note")
    expect(note).to_have_text("Game answers and names go to the AI service. Chat does not. Datenschutz")
    notice = note.get_by_role("link", name="Datenschutz")
    expect(notice).to_have_attribute("href", PRIVACY_NOTICE)
    expect(notice).to_have_attribute("target", "_blank")
    expect(notice).to_have_attribute("rel", "noopener")


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
    expect(guest.locator(TYPING)).to_have_count(0)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_question_opens_the_card_on_both_pages_and_only_the_starter_sees_reveal(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_service: FakeAgentService,
    form_factor: dict[str, Any],
) -> None:
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    agent_service.answer_next(Succeeds("Welcher Fluss fließt durch Lissabon?"))

    start_quiz(guest)

    for person in (host, guest):
        open_chat(person)
        card = person.locator(CARD)
        expect(card.locator(".qcard-tag")).to_have_text(QUESTION_ONE)
        expect(card.locator(".qcard-q")).to_have_text("Welcher Fluss fließt durch Lissabon?")
    expect(guest.locator(CARD).get_by_role("button", name=REVEAL)).to_be_visible()
    expect(host.locator(CARD).get_by_role("button", name=REVEAL)).to_have_count(0)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_starters_reveal_puts_the_reveal_line_in_both_chats_and_opens_the_next_question(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_service: FakeAgentService,
    form_factor: dict[str, Any],
) -> None:
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    agent_service.answer_next(
        Succeeds("Welcher Fluss fließt durch Lissabon?"), Succeeds("Der Tejo."), Succeeds("Zweite?")
    )
    start_quiz(guest)
    for person in (host, guest):
        open_chat(person)
        expect(person.locator(CARD)).to_be_visible()

    guest.locator(CARD).get_by_role("button", name=REVEAL).click()

    for person in (host, guest):
        lines = person.locator(GAME_MASTER_LINES)
        expect(lines).to_have_count(3)
        expect(lines.nth(1)).to_contain_text("Der Tejo.")
        expect(person.locator(CARD).locator(".qcard-tag")).to_have_text(f"Question 2 of {QUIZ_ROUNDS}")
        expect(person.locator(CARD).locator(".qcard-q")).to_have_text("Zweite?")


def answer_on_card(person: Page, text: str) -> None:
    card = person.locator(CARD)
    card.get_by_label(YOUR_ANSWER).fill(text)
    card.get_by_role("button", name=SEND).click()


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_everyone_answers_privately_and_the_reveal_follows_the_last_answer_on_its_own(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_service: FakeAgentService,
    member_of: Callable[[Page], SeededMember],
    form_factor: dict[str, Any],
) -> None:
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    host_name, guest_name = (member_of(person).account.username for person in (host, guest))
    reveal_line = f"Der Tejo! {host_name} hat recht, {guest_name} tippte auf Guadiana."
    agent_service.answer_next(
        Succeeds("Welcher Fluss fließt durch Lissabon?"), Succeeds(reveal_line), Succeeds("Zweite?")
    )
    start_quiz(host)
    for person in (host, guest):
        open_chat(person)
        expect(person.locator(CARD)).to_be_visible()

    answer_on_card(guest, "Mondego")

    expect(guest.locator(CARD).locator(STATUS)).to_have_text("Answer sent · 1 of 2 answered")
    expect(host.locator(CARD).locator(STATUS)).to_have_text("1 of 2 answered")
    for person in (host, guest):
        expect(person.locator("#chatMessages")).not_to_contain_text("Mondego")
    expect(host.get_by_text("Mondego")).to_have_count(0)
    answer_on_card(guest, "Guadiana")
    expect(guest.locator(CARD).locator(STATUS)).to_have_text("Answer sent · 1 of 2 answered")
    # The guest's socket is handled in order: once the host sees this line, the overwrite is taken.
    overwrite_taken = "Guadiana ist meine Antwort"
    guest.fill("#chatInput", overwrite_taken)
    guest.click("#chatSend")
    expect(host.locator("#chatMessages")).to_contain_text(overwrite_taken)
    answer_on_card(host, "Tejo")

    for person in (host, guest):
        expect(person.locator(GAME_MASTER_LINES)).to_have_count(3)
        expect(person.locator(GAME_MASTER_LINES).nth(1)).to_contain_text(reveal_line)
        expect(person.locator(CARD).locator(".qcard-tag")).to_have_text(f"Question 2 of {QUIZ_ROUNDS}")
        expect(person.locator("#chatMessages")).not_to_contain_text("Mondego")
        close_chat(person)
        expect(open_more(person).locator(MORE_PROGRESS)).to_have_text(f"Question 2 of {QUIZ_ROUNDS}")
    expect(open_more(host).locator(START_QUIZ)).to_contain_text("End quiz")
    expect(open_more(guest).locator(START_QUIZ)).to_contain_text("Quiz running")
    reveal_request = agent_service.runs[-2].request.turn.messages[-1].text
    assert answers_in_reveal(reveal_request) == [
        {"name": guest_name, "antwort": "Guadiana"},
        {"name": host_name, "antwort": "Tejo"},
    ]
    assert "Mondego" not in str(agent_service.runs[-2].request)


def test_an_answer_over_the_cap_shows_only_its_sender_the_toast_and_no_answer_sent_mark(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_service: FakeAgentService,
) -> None:
    host, guest = call_of_two(open_person, e2e_server.base_url, page_errors)
    agent_service.answer_next(Succeeds("Welcher Fluss fließt durch Lissabon?"))
    start_quiz(host)
    for person in (host, guest):
        open_chat(person)
        expect(person.locator(CARD)).to_be_visible()

    answer_on_card(guest, "x" * (GAME_ANSWER_MAX_CHARACTERS + 1))

    expect(guest.locator(TOASTS)).to_contain_text("Your answer is too long. Shorten it and send again.")
    answer_on_card(host, "Tejo")
    # The host's own count comes after any toast the guest's answer could have sent the host.
    expect(host.locator(CARD).locator(STATUS)).to_have_text("Answer sent · 1 of 2 answered")
    expect(host.locator(TOASTS)).not_to_contain_text("too long")
    expect(guest.locator(CARD).locator(STATUS)).to_have_text("1 of 2 answered")
    expect(guest.locator(CARD).locator(STATUS)).not_to_have_class(SENT)


def test_an_answer_over_the_cap_after_a_given_answer_keeps_the_answer_sent_mark(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_service: FakeAgentService,
) -> None:
    host, guest = call_of_two(open_person, e2e_server.base_url, page_errors)
    agent_service.answer_next(Succeeds("Welcher Fluss fließt durch Lissabon?"))
    start_quiz(host)
    for person in (host, guest):
        open_chat(person)
        expect(person.locator(CARD)).to_be_visible()
    answer_on_card(guest, "Porto")
    expect(guest.locator(CARD).locator(STATUS)).to_have_text("Answer sent · 1 of 2 answered")

    answer_on_card(guest, "x" * (GAME_ANSWER_MAX_CHARACTERS + 1))

    expect(guest.locator(TOASTS)).to_contain_text("Your answer is too long. Shorten it and send again.")
    expect(guest.locator(CARD).locator(STATUS)).to_have_text("Answer sent · 1 of 2 answered")
    expect(guest.locator(CARD).locator(STATUS)).to_have_class(SENT)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_an_answer_refused_as_closed_leaves_no_answer_sent_mark_on_its_sender_card(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_service: FakeAgentService,
    form_factor: dict[str, Any],
) -> None:
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    reveal_run = Holds(then=Succeeds("Der Tejo."))
    agent_service.answer_next(Succeeds("Welcher Fluss fließt durch Lissabon?"), reveal_run, Succeeds("Zweite?"))
    start_quiz(host)
    for person in (host, guest):
        open_chat(person)
        expect(person.locator(CARD)).to_be_visible()
    answer_on_card(host, "Tejo")
    expect(guest.locator(CARD).locator(STATUS)).to_have_text("1 of 2 answered")
    host.locator(CARD).get_by_role("button", name=REVEAL).click()
    expect(guest.locator(CARD).get_by_label(YOUR_ANSWER)).to_be_disabled()
    guest.locator(CARD).locator(".qcard-input, .qcard-send").evaluate_all(
        "fields => fields.forEach(field => { field.disabled = false; })"
    )

    answer_on_card(guest, "Mondego")

    expect(guest.locator(TOASTS)).to_contain_text("This question is already closed.")
    expect(guest.locator(CARD).locator(STATUS)).to_have_text("1 of 2 answered")
    reveal_run.release.set()


def test_a_page_that_joins_during_a_question_has_no_card_and_plays_from_the_next(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], agent_service: FakeAgentService
) -> None:
    host, guest, invite_link = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)
    agent_service.answer_next(Succeeds("Erste?"), Succeeds("Der Tejo."), Succeeds("Zweite?"))
    start_quiz(host)
    for person in (host, guest):
        open_chat(person)
        expect(person.locator(CARD)).to_be_visible()
    late = join(open_person, invite_link, DESKTOP, (), page_errors)
    open_chat(late)

    expect(late.locator(CARD)).to_have_count(0)
    answer_on_card(host, "A")
    answer_on_card(guest, "B")

    expect(late.locator(CARD).locator(".qcard-tag")).to_have_text(f"Question 2 of {QUIZ_ROUNDS}")
    answer_on_card(late, "C")
    expect(late.locator(CARD).locator(STATUS)).to_have_text("Answer sent · 1 of 3 answered")


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_both_pages_show_the_game_master_thinking_while_the_reveal_runs_and_until_the_next_question_opens(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_service: FakeAgentService,
    form_factor: dict[str, Any],
) -> None:
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    reveal_run, next_question_run = Holds(then=Succeeds("Der Tejo.")), Holds(then=Succeeds("Zweite?"))
    agent_service.answer_next(Succeeds("Welcher Fluss fließt durch Lissabon?"), reveal_run, next_question_run)
    start_quiz(guest)
    for person in (host, guest):
        open_chat(person)
        expect(person.locator(CARD)).to_be_visible()

    guest.locator(CARD).get_by_role("button", name=REVEAL).click()

    for person in (host, guest):
        expect(person.locator(TYPING)).to_be_visible()
    expect(guest.locator(CARD).get_by_role("button", name=REVEAL)).to_be_disabled()
    for person in (host, guest):
        close_chat(person)
        item = open_more(person).locator(START_QUIZ)
        expect(item).to_have_text("Game master is thinking…", use_inner_text=True)
        expect(item).to_be_disabled()
        open_chat(person)
    reveal_run.release.set()
    for person in (host, guest):
        expect(person.locator(GAME_MASTER_LINES).last).to_contain_text("Der Tejo.")
        expect(person.locator(TYPING)).to_be_visible()
        expect(person.locator(CARD)).to_have_count(0)
    next_question_run.release.set()
    for person in (host, guest):
        expect(person.locator(CARD).locator(".qcard-q")).to_have_text("Zweite?")
        expect(person.locator(TYPING)).to_have_count(0)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_five_rounds_end_in_the_game_masters_final_line_on_both_pages_and_free_start_quiz(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_service: FakeAgentService,
    form_factor: dict[str, Any],
) -> None:
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    lines = [text for number in range(1, QUIZ_ROUNDS + 1) for text in (f"Frage {number}?", f"Antwort {number}.")] + [
        "Ihr wart großartig."
    ]
    final_score_run = Holds(then=Succeeds(lines[-1]))
    agent_service.answer_next(*map(Succeeds, lines[:-1]), final_score_run)
    start_quiz(guest)
    for person in (host, guest):
        open_chat(person)

    for number in range(1, QUIZ_ROUNDS + 1):
        for person in (host, guest):
            expect(person.locator(CARD).locator(".qcard-tag")).to_have_text(f"Question {number} of {QUIZ_ROUNDS}")
        guest.locator(CARD).get_by_role("button", name=REVEAL).click()

    for person in (host, guest):
        expect(person.locator(GAME_MASTER_LINES).last).to_contain_text(f"Antwort {QUIZ_ROUNDS}.")
        expect(person.locator(TYPING)).to_be_visible()
        expect(person.locator(CARD)).to_have_count(0)
    final_score_run.release.set()

    for person in (host, guest):
        expect(person.locator(GAME_MASTER_LINES)).to_contain_text(lines)
        expect(person.locator(CARD)).to_have_count(0)
        close_chat(person)
        open_more(person)
        expect_idle(person)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_while_a_question_is_open_more_says_end_quiz_to_the_starter_and_quiz_running_to_the_others(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_service: FakeAgentService,
    form_factor: dict[str, Any],
) -> None:
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    agent_service.answer_next(Succeeds("Welcher Fluss fließt durch Lissabon?"))

    start_quiz(host)

    starters_item = open_more(host).locator(START_QUIZ)
    expect(starters_item).to_have_text(END_QUIZ, use_inner_text=True)
    expect(starters_item).to_be_enabled()
    others_item = open_more(guest).locator(START_QUIZ)
    expect(others_item).to_have_text(QUIZ_RUNNING, use_inner_text=True)
    expect(others_item).to_be_disabled()


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_starters_end_quiz_shows_everyone_quiz_ended_and_frees_start_quiz(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_service: FakeAgentService,
    form_factor: dict[str, Any],
) -> None:
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    agent_service.answer_next(Succeeds("Welcher Fluss fließt durch Lissabon?"))
    runs_before = len(agent_service.runs)
    start_quiz(guest)
    for person in (host, guest):
        open_chat(person)
        expect(person.locator(CARD)).to_be_visible()

    for person in (host, guest):
        close_chat(person)
    end_quiz(guest)

    for person in (host, guest):
        expect_toast(person, QUIZ_ENDED, X_ICON_PATH)
        expect(person.locator(CARD)).to_have_count(0)
        expect(open_more(person).locator(START_QUIZ)).to_contain_text("Start quiz")
        expect_idle(person)
    assert len(agent_service.runs) == runs_before + 1


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_starters_leave_shows_everyone_quiz_ended_with_the_name_and_frees_start_quiz(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_service: FakeAgentService,
    member_of: Callable[[Page], SeededMember],
    form_factor: dict[str, Any],
) -> None:
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    guest_name = member_of(guest).account.username
    agent_service.answer_next(Succeeds("Welcher Fluss fließt durch Lissabon?"))
    start_quiz(guest)
    for person in (host, guest):
        open_chat(person)
        expect(person.locator(CARD)).to_be_visible()

    close_chat(guest)
    leave_the_call(guest)

    expect_toast(host, f"Quiz ended: {guest_name} left.", X_ICON_PATH)
    expect(host.locator(CARD)).to_have_count(0)
    close_chat(host)
    expect(open_more(host).locator(START_QUIZ)).to_contain_text("Start quiz")
    expect_idle(host)


def test_a_players_leave_shrinks_the_count_and_the_reveal_follows_once_everyone_still_there_has_answered(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], agent_service: FakeAgentService
) -> None:
    host, guest, invite_link = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)
    third = join(open_person, invite_link, DESKTOP, (), page_errors)
    reveal_run = Holds(then=Succeeds("Der Tejo."))
    agent_service.answer_next(Succeeds("Welcher Fluss fließt durch Lissabon?"), reveal_run, Succeeds("Zweite?"))
    start_quiz(host)
    for person in (host, guest, third):
        open_chat(person)
        expect(person.locator(CARD)).to_be_visible()
    answer_on_card(host, "Tejo")
    answer_on_card(guest, "Mondego")
    for person in (host, guest):
        expect(person.locator(CARD).locator(STATUS)).to_have_text("Answer sent · 2 of 3 answered")

    leave_the_call(third)

    for person in (host, guest):
        expect(person.locator(CARD).locator(STATUS)).to_have_text("Answer sent · 2 of 2 answered")
    assert reveal_run.started.wait()
    reveal_run.release.set()
    for person in (host, guest):
        expect(person.locator(GAME_MASTER_LINES).nth(1)).to_contain_text("Der Tejo.")
        expect(person.locator(CARD).locator(".qcard-q")).to_have_text("Zweite?")


def test_the_hosts_end_quiz_ends_a_guests_quiz_for_everyone(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], agent_service: FakeAgentService
) -> None:
    host, guest = call_of_two(open_person, e2e_server.base_url, page_errors)
    agent_service.answer_next(Succeeds("Welcher Fluss fließt durch Lissabon?"))
    start_quiz(guest)
    expect(open_more(host).locator(START_QUIZ)).to_have_text(END_QUIZ, use_inner_text=True)

    end_quiz(host)

    for person in (host, guest):
        expect_toast(person, QUIZ_ENDED, X_ICON_PATH)
        expect(open_more(person).locator(START_QUIZ)).to_contain_text("Start quiz")
        expect_idle(person)


def test_a_guests_crafted_start_reveal_and_end_are_each_refused_to_the_guest_alone(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], agent_service: FakeAgentService
) -> None:
    host, guest = call_of_two(open_person, e2e_server.base_url, page_errors)
    agent_service.answer_next(Succeeds("Welcher Fluss fließt durch Lissabon?"))
    start_quiz(host)
    open_chat(host)
    expect(host.locator(CARD)).to_be_visible()
    refusals = {
        GameSignal.GAME_START: "A quiz is already running.",
        GameSignal.GAME_REVEAL: "Only the person who started the quiz can reveal.",
        GameSignal.GAME_END: "Only the person who started the quiz or the host can end it.",
    }

    for signal_type, toast in refusals.items():
        send_frame(guest, signal_type)
        expect(guest.locator(TOASTS)).to_contain_text(toast)

    answer_on_card(host, "Tejo")
    # The host's own count comes after any frame the guest's crafted frames could have sent the host.
    expect(host.locator(CARD).locator(STATUS)).to_have_text("Answer sent · 1 of 2 answered")
    expect(host.locator(TOASTS)).to_be_empty()
    expect(host.locator(CARD)).to_be_visible()
    expect(open_more(host).locator(START_QUIZ)).to_have_text(END_QUIZ, use_inner_text=True)


def test_a_refused_reveal_shows_the_starter_the_reason_and_everyone_else_quiz_ended(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], agent_service: FakeAgentService
) -> None:
    host, guest = call_of_two(open_person, e2e_server.base_url, page_errors)
    agent_service.answer_next(
        Succeeds("Welcher Fluss fließt durch Lissabon?"), Rejects(HTTPStatus.FORBIDDEN, RejectionCode.NOT_ENABLED)
    )
    start_quiz(host)
    for person in (host, guest):
        open_chat(person)
        expect(person.locator(CARD)).to_be_visible()

    host.locator(CARD).get_by_role("button", name=REVEAL).click()

    expect(host.locator(TOASTS)).to_have_text("Your account has no AI access.")
    expect_toast(guest, QUIZ_ENDED, X_ICON_PATH)
    for person in (host, guest):
        expect(person.locator(CARD)).to_have_count(0)
        open_more(person)
        expect_idle(person)
    expect(host.locator(TOASTS)).not_to_contain_text(QUIZ_ENDED)


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
