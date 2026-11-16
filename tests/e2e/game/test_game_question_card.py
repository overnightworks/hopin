from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.game.game_page import (
    CARD,
    SENT,
    STATUS,
    YOUR_ANSWER,
    answer_count_status,
    answer_on_card,
    answer_sent_status,
    open_chat,
    question_progress,
    start_quiz,
)
from tests.e2e.support.calls import open_call
from tests.e2e.support.people import FORM_FACTORS
from tests.game.support.fake_agent_backend import Succeeds

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.server import RunningServer
    from tests.game.support.fake_agent_backend import FakeAgentBackend

pytestmark = pytest.mark.e2e

# The server's answer count is a socket frame, a task of its own: no microtask can see it arrive.
SEND_AND_READ_STATUS = """
async (card) => {
    card.querySelector(".qcard-send").click();
    await Promise.resolve();
    const status = card.querySelector(".qcard-status");
    return { sent: status.classList.contains("sent"), text: status.textContent.trim() };
}
"""

RECORD_THE_STATUS_LINE = """
() => {
    window.statusLineSeen = [];
    const record = () => {
        const status = document.querySelector("#chatPanel .qcard-status");
        if (!status) return;
        const sent = status.classList.contains("sent");
        window.statusLineSeen.push({ sent, text: status.textContent.trim() });
    };
    const everything = { subtree: true, childList: true, characterData: true, attributes: true };
    new MutationObserver(record).observe(document.querySelector("#chatPanel"), everything);
}
"""


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_player_whose_answer_completes_the_round_never_reads_a_count_without_their_own_answer(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_backend: FakeAgentBackend,
    form_factor: dict[str, Any],
) -> None:
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    agent_backend.answer_next(
        Succeeds("Welcher Fluss fließt durch Lissabon?"), Succeeds("Der Tejo!"), Succeeds("Zweite?")
    )
    start_quiz(host)
    for person in (host, guest):
        open_chat(person)
        expect(person.locator(CARD)).to_be_visible()
    answer_on_card(host, "Tejo")
    expect(guest.locator(CARD).locator(STATUS)).to_have_text(answer_count_status(1, 2))
    guest.evaluate(RECORD_THE_STATUS_LINE)

    answer_on_card(guest, "Mondego")

    expect(guest.locator(CARD).locator(".qcard-tag")).to_have_text(question_progress(2))
    seen = guest.evaluate("window.statusLineSeen")
    own_answer_counts = [line for line in seen if line["sent"]]
    assert {line["text"] for line in own_answer_counts} == {answer_sent_status(2, 2)}, seen


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_answer_sent_mark_never_shows_without_its_text_right_after_the_send(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_backend: FakeAgentBackend,
    form_factor: dict[str, Any],
) -> None:
    """The status line's check mark and its text come in one update, the text never trailing the mark."""
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    agent_backend.answer_next(Succeeds("Welcher Fluss fließt durch Lissabon?"))
    start_quiz(host)
    for person in (host, guest):
        open_chat(person)
        expect(person.locator(CARD)).to_be_visible()

    card = guest.locator(CARD)
    card.get_by_label(YOUR_ANSWER).fill("Mondego")
    state = card.evaluate(SEND_AND_READ_STATUS)

    assert not state["sent"] or state["text"] != "", f"the mark shows without its text: {state}"
    expect(card.locator(STATUS)).to_have_text(answer_sent_status(1, 2))
    expect(card.locator(STATUS)).to_have_class(SENT)
