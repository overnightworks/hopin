import re
from typing import TYPE_CHECKING, Any

from playwright.sync_api import Page, expect

from hopin.game.quiz import QUIZ_ROUNDS
from tests.e2e.support.calls import open_call, open_more
from tests.e2e.support.people import DESKTOP
from tests.e2e.support.signaling import FrameInjector
from tests.support.words import text

if TYPE_CHECKING:
    from collections.abc import Callable

START_QUIZ = "#gameStartBtn"
MUTE_VOICE = "#gameVoiceBtn"
GAME_MASTER_LINES = "#chatMessages .game-master:not(.typing)"
TYPING = "#chatMessages .game-master.typing"
CARD = "#chatPanel .qcard"
STATUS = ".qcard-status"
SENT = re.compile(r"\bsent\b")
GAME_MASTER_NAME = text("de", "game.masterName")
GAME_MASTER_NAME_GAP = {"name": GAME_MASTER_NAME}
START_QUIZ_LABEL = text("de", "game.startButton")
QUIZ_RUNNING_LABEL = text("de", "game.runningLabel")
END_QUIZ_LABEL = text("de", "game.endButton")
THINKING_BUTTON_LABEL = text("de", "game.thinkingButton", **GAME_MASTER_NAME_GAP)
THINKING_STATUS_LABEL = text("de", "game.thinkingLabel", **GAME_MASTER_NAME_GAP)
QUESTION_CARD_LABEL = text("de", "game.questionCardLabel")
REVEAL = text("de", "game.revealButton")
SEND = text("de", "game.sendButton")
YOUR_ANSWER = text("de", "game.answerLabel")
QUIZ_ENDED = text("de", "game.endedToast")


def question_progress(round_number: int) -> str:
    return text("de", "game.questionProgress", round=str(round_number), rounds=str(QUIZ_ROUNDS))


def answer_count_status(answered: int, players: int) -> str:
    return text("de", "game.answerCountStatus", answered=str(answered), players=str(players))


def answer_sent_status(answered: int, players: int) -> str:
    return text("de", "game.answerSentCountStatus", answered=str(answered), players=str(players))


def start_quiz(person: Page) -> None:
    open_more(person)
    person.click(START_QUIZ)


def end_quiz(person: Page) -> None:
    open_more(person)
    person.click(START_QUIZ)


def open_chat(person: Page) -> None:
    if person.locator("#moreMenu").is_visible():
        person.keyboard.press("Escape")
    person.click("#chatBtn")
    expect(person.locator("#chatPanel")).to_be_visible()


def close_chat(person: Page) -> None:
    person.click("#chatClose")
    expect(person.locator("#chatPanel")).to_be_hidden()


def call_of_two(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], **options: Any
) -> tuple[Page, Page]:
    host, guest, _ = open_call(open_person, base_url, page_errors, {**DESKTOP, **options})
    return host, guest


def relaying_frames(open_person: Callable[..., Page], relays: list[FrameInjector]) -> Callable[..., Page]:
    """Open people whose signaling sockets run through a FrameInjector, collected in the order they open."""

    def open_relayed(**options: Any) -> Page:
        person = open_person(**options)
        relays.append(FrameInjector(person))
        return person

    return open_relayed


def answer_on_card(person: Page, text: str) -> None:
    card = person.locator(CARD)
    card.get_by_label(YOUR_ANSWER).fill(text)
    card.get_by_role("button", name=SEND).click()
