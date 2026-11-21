from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import Page, expect

from hopin.game.quiz import QUIZ_ROUNDS, Judgement
from hopin.words import text
from tests.e2e.game.game_page import (
    CARD,
    GAME_MASTER_LINES,
    GAME_MASTER_NAME_GAP,
    QUIZ_ENDED,
    SCORE,
    STATUS,
    answer_on_card,
    answer_sent_status,
    call_of_two,
    final_score_line,
    open_chat,
    question_progress,
    score_line,
    start_quiz,
)
from tests.e2e.support.page_assertions import TOASTS, X_ICON_PATH, expect_toast
from tests.game.support.fake_agent_backend import Succeeds

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.server import RunningServer
    from tests.game.support.fake_agent_backend import FakeAgentBackend
    from tests.support.signed_in import SeededMember

pytestmark = pytest.mark.e2e


def verdict(number: int, judgement: Judgement) -> dict[str, object]:
    return {"nr": number, "judgement": judgement.value}


def answered_in_turn(host: Page, guest: Page) -> None:
    """The host's answer reaches the server first, so its nr is 1."""
    answer_on_card(host, "Tejo")
    expect(host.locator(CARD).locator(STATUS)).to_have_text(answer_sent_status(1, 2))
    answer_on_card(guest, "Mondego")


def answered_by_both(host: Page, guest: Page) -> None:
    for person in (host, guest):
        open_chat(person)
        expect(person.locator(CARD)).to_be_visible()
    answered_in_turn(host, guest)


def test_an_answer_judged_wrong_gives_its_person_no_point_and_both_pages_show_the_counted_score(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_backend: FakeAgentBackend,
    member_of: Callable[[Page], SeededMember],
) -> None:
    """GM-40: An answer judged wrong gives its person no point and both pages show the counted score."""
    host, guest = call_of_two(open_person, e2e_server.base_url, page_errors)
    host_name, guest_name = (member_of(person).account.username for person in (host, guest))
    verdicts = (verdict(1, Judgement.CORRECT), verdict(2, Judgement.WRONG))
    agent_backend.answer_next(
        Succeeds("Welcher Fluss fließt durch Lissabon?"), Succeeds("Der Tejo!", verdicts=verdicts), Succeeds("Zweite?")
    )
    start_quiz(host)

    answered_by_both(host, guest)

    for person in (host, guest):
        expect(person.locator(CARD).locator(SCORE)).to_have_text(score_line((host_name, 1), (guest_name, 0)))


def test_malformed_verdicts_show_the_starter_the_failure_and_no_page_a_score(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], agent_backend: FakeAgentBackend
) -> None:
    """GM-40: Malformed verdicts show the starter the failure and no page a score."""
    host, guest = call_of_two(open_person, e2e_server.base_url, page_errors)
    malformed = ({"nr": 1, "judgement": "maybe"},)
    agent_backend.answer_next(
        Succeeds("Welcher Fluss fließt durch Lissabon?"), Succeeds("Der Tejo!", verdicts=malformed)
    )
    start_quiz(host)

    answered_by_both(host, guest)

    expect(host.locator(TOASTS)).to_contain_text(text("de", "game.unavailableToast", **GAME_MASTER_NAME_GAP))
    expect_toast(guest, QUIZ_ENDED, X_ICON_PATH)
    for person in (host, guest):
        expect(person.locator(SCORE)).to_have_count(0)
        expect(person.locator(GAME_MASTER_LINES)).to_have_count(1)


def test_the_quiz_ends_in_a_final_score_line_equal_to_the_counted_verdicts(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_backend: FakeAgentBackend,
    member_of: Callable[[Page], SeededMember],
) -> None:
    """GM-24: The quiz ends in a final score line equal to the counted verdicts."""
    host, guest = call_of_two(open_person, e2e_server.base_url, page_errors)
    host_name, guest_name = (member_of(person).account.username for person in (host, guest))
    guest_correct = 2
    rounds = [
        (
            Succeeds(f"Frage {number}?"),
            Succeeds(
                f"Antwort {number}.",
                verdicts=(
                    verdict(1, Judgement.CORRECT),
                    verdict(2, Judgement.CORRECT if number <= guest_correct else Judgement.WRONG),
                ),
            ),
        )
        for number in range(1, QUIZ_ROUNDS + 1)
    ]
    agent_backend.answer_next(*(run for played in rounds for run in played), Succeeds("Ihr wart großartig."))
    start_quiz(host)

    for person in (host, guest):
        open_chat(person)
    for number in range(1, QUIZ_ROUNDS + 1):
        for person in (host, guest):
            expect(person.locator(CARD).locator(".qcard-tag")).to_have_text(question_progress(number))
        answered_in_turn(host, guest)

    for person in (host, guest):
        expect(person.locator(GAME_MASTER_LINES).last).to_contain_text(
            final_score_line((host_name, QUIZ_ROUNDS), (guest_name, guest_correct))
        )
