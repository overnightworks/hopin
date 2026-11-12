import re
from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.game.game_page import CARD, YOUR_ANSWER, answer_sent_status, open_chat, start_quiz
from tests.e2e.support.calls import open_call
from tests.e2e.support.people import FORM_FACTORS
from tests.game.support.fake_agent_backend import Succeeds

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.server import RunningServer
    from tests.game.support.fake_agent_backend import FakeAgentBackend

pytestmark = pytest.mark.e2e

STATUS = ".qcard-status"
SENT = re.compile(r"\bsent\b")
# The server's answer count is a socket frame, a task of its own: no microtask can see it arrive.
SEND_AND_READ_STATUS = """
async (card) => {
    card.querySelector(".qcard-send").click();
    await Promise.resolve();
    const status = card.querySelector(".qcard-status");
    return { sent: status.classList.contains("sent"), text: status.textContent.trim() };
}
"""


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
