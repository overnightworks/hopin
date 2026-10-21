from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page, expect

from hopin.config.constants import AUTH_CALLBACK_PATH, AUTH_START_PATH
from tests.e2e.conftest import DESKTOP, FORM_FACTORS, PHONE, RunningServer, expect_both_legal_links, record_page_errors

pytestmark = pytest.mark.e2e

FORGED_CALLBACK = f"{AUTH_CALLBACK_PATH}?state=forged&code=x"
CARD_ACTIONS = ".login-card .actions .btn"


def open_login_failed(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], form_factor: dict[str, Any]
) -> Page:
    person = record_page_errors(open_person(signed_in=False, **form_factor), page_errors)
    response = person.goto(f"{e2e_server.base_url}{FORGED_CALLBACK}")
    assert response is not None
    assert response.status == 403
    return person


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_failed_sign_in_shows_login_failed_with_one_way_out_and_both_legal_links(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    person = open_login_failed(open_person, e2e_server, page_errors, form_factor)

    card = person.locator(".login-card")
    expect(card.locator(".wordmark")).to_have_text("HopIn")
    expect(card.get_by_role("heading", name="Login failed")).to_be_visible()
    expect(card.locator(".banner--fail")).to_have_text("Sign-in did not work. Please try again.")
    expect(person.locator(CARD_ACTIONS)).to_have_text(["Try again"])
    expect_both_legal_links(person.locator(".legal-links"))
    assert person.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_try_again_is_as_wide_as_the_card_on_a_phone_and_narrower_on_a_desktop(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str]
) -> None:
    widths = {}
    for name, form_factor in {"phone": PHONE, "desktop": DESKTOP}.items():
        person = open_login_failed(open_person, e2e_server, page_errors, form_factor)
        widths[name] = person.evaluate(
            """() => {
                const card = document.querySelector(".login-card");
                const style = getComputedStyle(card);
                const content = card.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
                return [document.querySelector(".login-card .actions .btn").getBoundingClientRect().width, content];
            }"""
        )

    phone_button, phone_content = widths["phone"]
    desktop_button, desktop_content = widths["desktop"]
    assert phone_button == pytest.approx(phone_content, abs=1)
    assert desktop_button < desktop_content


def test_try_again_starts_a_new_sign_in_at_the_issuer(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str]
) -> None:
    person = open_login_failed(open_person, e2e_server, page_errors, DESKTOP)
    documents = []
    person.on("request", lambda request: request.is_navigation_request() and documents.append(request))

    with person.expect_request(lambda request: urlsplit(request.url).hostname == "issuer.test.invalid"):
        person.locator(CARD_ACTIONS).click()

    urls = [urlsplit(request.url) for request in documents]
    assert urls[0].path == AUTH_START_PATH
    assert urls[0].query == ""
    assert urls[-1].hostname == "issuer.test.invalid"
    assert documents[0].response().status == 303
