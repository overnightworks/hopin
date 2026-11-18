"""HopIn's render pass: each page once in each further language, at the phone and the desktop width. No catalog key,
function or object shows as text, `<html lang>` names the language and no text runs over its box."""

from typing import TYPE_CHECKING, Any, get_args

import pytest
from playwright.sync_api import Page, expect

from hopin.config.constants import AUTH_CALLBACK_PATH, LOGIN_PATH, NO_ACCESS_PATH
from hopin.words import LANGUAGES, SOURCE_LANGUAGE, Key, text
from tests.e2e.support.calls import call_link_of, press_through_the_prejoin, record_page_errors
from tests.e2e.support.people import FORM_FACTORS

if TYPE_CHECKING:
    from collections.abc import Callable

    from hopin.words import Language
    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

FURTHER_LANGUAGES: list[Language] = [language for language in LANGUAGES if language != SOURCE_LANGUAGE]
CATALOG_KEYS: tuple[str, ...] = get_args(Key.__value__)
FORGED_CALLBACK = f"{AUTH_CALLBACK_PATH}?state=forged&code=x"
FINDINGS_OF_A_RENDER = """(keys) => {
    const shown = document.body.innerText;
    const findings = keys.filter((key) => shown.includes(key)).map((key) => `key ${key}`);
    if (shown.includes("[object Object]")) findings.push("[object Object]");
    // The first line of a function's source, shown where a gap function was never called.
    if (/=>|\\bfunction\\b\\s*\\(/.test(shown)) findings.push("function source");
    const overflowing = [...document.body.querySelectorAll("*")]
        .filter((element) => [...element.childNodes].some((node) => node.nodeType === 3 && node.textContent.trim()))
        .filter((element) => !["auto", "scroll"].includes(getComputedStyle(element).overflowX))
        // An ellipsis is a text the design clips to its box on purpose.
        .filter((element) => getComputedStyle(element).textOverflow !== "ellipsis")
        // A one pixel box is a label only a screen reader reads.
        .filter((element) => element.clientWidth > 1 && element.scrollWidth > element.clientWidth)
        .map((element) => `${element.tagName.toLowerCase()}.${element.className} ${element.textContent.trim()}`);
    return [...findings, ...overflowing.map((element) => `overflow ${element}`)];
}"""


def assert_renders_in(person: Page, language: Language) -> None:
    expect(person.locator("html")).to_have_attribute("lang", language)
    assert person.evaluate(FINDINGS_OF_A_RENDER, list(CATALOG_KEYS)) == []


@pytest.mark.parametrize("language", FURTHER_LANGUAGES)
@pytest.mark.parametrize("form_factor", FORM_FACTORS)
@pytest.mark.parametrize(
    ("path", "status"),
    [
        pytest.param(LOGIN_PATH, 200, id="signed-out"),
        pytest.param(FORGED_CALLBACK, 403, id="login-failed"),
        pytest.param(NO_ACCESS_PATH, 200, id="no-access"),
    ],
)
def test_each_sign_in_page_renders_in_the_language_the_browser_asks_for(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    form_factor: dict[str, Any],
    language: Language,
    path: str,
    status: int,
) -> None:
    person = record_page_errors(open_person(signed_in=False, locale=language, **form_factor), page_errors)

    response = person.goto(f"{e2e_server.base_url}{path}")

    assert response is not None
    assert response.status == status
    assert_renders_in(person, language)


@pytest.mark.parametrize("language", FURTHER_LANGUAGES)
@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_home_prejoin_call_and_end_screens_render_in_the_language_the_browser_asks_for(
    open_person: Callable[..., Page],
    base_url: str,
    page_errors: list[str],
    form_factor: dict[str, Any],
    language: Language,
) -> None:
    person = record_page_errors(open_person(locale=language, **form_factor), page_errors)

    person.goto(f"{base_url}/")
    expect(person.locator("#homeStartBtn")).to_have_text(text(language, "home.startButton"))
    assert_renders_in(person, language)

    person.click("#homeStartBtn")
    expect(person.locator("[data-screen=prejoin]")).to_be_visible()
    assert_renders_in(person, language)

    press_through_the_prejoin(person)
    call_link_of(person)
    expect(person.locator("[data-screen=call]")).to_be_visible()
    assert_renders_in(person, language)

    person.click("#leaveBtn")
    person.click("#leaveYes")
    expect(person.locator("[data-screen=end]")).to_be_visible()
    assert_renders_in(person, language)
