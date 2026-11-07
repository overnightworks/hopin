from playwright.sync_api import Page, expect

from tests.e2e.support.calls import open_more

START_QUIZ = "#gameStartBtn"
GAME_MASTER_LINES = "#chatMessages .game-master:not(.typing)"
TYPING = "#chatMessages .game-master.typing"
CARD = "#chatPanel .qcard"
SEND = "Send"
YOUR_ANSWER = "Your answer"


def start_quiz(person: Page) -> None:
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


def answer_on_card(person: Page, text: str) -> None:
    card = person.locator(CARD)
    card.get_by_label(YOUR_ANSWER).fill(text)
    card.get_by_role("button", name=SEND).click()
