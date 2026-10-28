from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Page, expect

from hopin.config.constants import AUTH_CALLBACK_PATH, AUTH_START_PATH, LOGIN_PATH, LOGOUT_PATH, NO_ACCESS_PATH
from tests.e2e.support.calls import record_page_errors
from tests.e2e.support.page_assertions import expect_both_legal_links
from tests.e2e.support.people import DESKTOP, FORM_FACTORS, PHONE
from tests.support.sign_in_settings import TEST_ISSUER

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

FORGED_CALLBACK = f"{AUTH_CALLBACK_PATH}?state=forged&code=x"
CARD_ACTIONS = ".login-card .actions .btn"
END_SESSION_PATH = "/realms/test/protocol/openid-connect/logout"
END_SESSION_URL = f"{TEST_ISSUER}/protocol/openid-connect/logout"
ACTION_AND_CARD_CONTENT_WIDTHS = """() => {
    const card = document.querySelector(".login-card");
    const style = getComputedStyle(card);
    const content = card.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    return [document.querySelector(".login-card .btn").getBoundingClientRect().width, content];
}"""


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
    expect(card.get_by_role("heading", name="Login failed", exact=True)).to_be_visible()
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
        widths[name] = person.evaluate(ACTION_AND_CARD_CONTENT_WIDTHS)

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


def open_signed_out_page(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    path: str,
    form_factor: dict[str, Any],
) -> Page:
    person = record_page_errors(open_person(signed_in=False, **form_factor), page_errors)
    person.on("console", lambda message: page_errors.append(f"{message.type}: {message.text}"))
    response = person.goto(f"{e2e_server.base_url}{path}")
    assert response is not None
    assert response.status == 200
    return person


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
@pytest.mark.parametrize(
    ("path", "title", "heading", "action"),
    [
        (LOGIN_PATH, "HopIn - Signed out", "You are logged out.", "Log in"),
        (NO_ACCESS_PATH, "HopIn - No access", "No access", "Log out"),
    ],
)
def test_a_sign_in_state_page_shows_its_heading_its_one_action_and_both_legal_links(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    path: str,
    title: str,
    heading: str,
    action: str,
    form_factor: dict[str, Any],
) -> None:
    person = open_signed_out_page(open_person, e2e_server, page_errors, path, form_factor)

    expect(person).to_have_title(title)
    card = person.locator(".login-card")
    expect(card.locator(".wordmark")).to_have_text("HopIn")
    expect(card.get_by_role("heading", name=heading, exact=True)).to_be_visible()
    expect(card.locator(".btn")).to_have_text([action])
    expect_both_legal_links(person.locator(".legal-links"))
    assert person.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_the_no_access_page_says_the_account_is_signed_in_but_has_no_access(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str]
) -> None:
    person = open_signed_out_page(open_person, e2e_server, page_errors, NO_ACCESS_PATH, DESKTOP)

    expect(person.locator(".login-card p")).to_have_text("You are signed in, but this account has no access to HopIn.")


def test_log_out_is_as_wide_as_the_card_on_a_phone_and_narrower_on_a_desktop(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str]
) -> None:
    widths = {
        name: open_signed_out_page(open_person, e2e_server, page_errors, NO_ACCESS_PATH, form_factor).evaluate(
            ACTION_AND_CARD_CONTENT_WIDTHS
        )
        for name, form_factor in {"phone": PHONE, "desktop": DESKTOP}.items()
    }

    phone_button, phone_content = widths["phone"]
    desktop_button, desktop_content = widths["desktop"]
    assert phone_button == pytest.approx(phone_content, abs=1)
    assert desktop_button < desktop_content


ISSUER_NOT_REACHABLE = "net::ERR_NAME_NOT_RESOLVED"


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_log_out_ends_the_session_and_the_loop_stops_at_the_issuers_end_session_whose_return_is_not_driven_here(
    open_person: Callable[..., Page],
    member_of: Callable[[Page], Any],
    e2e_server: RunningServer,
    page_errors: list[str],
    form_factor: dict[str, Any],
) -> None:
    """The loop stops at the issuer: the test issuer does not resolve, so the return to Signed out is #25 row 4f's."""
    person = record_page_errors(open_person(**form_factor), page_errors)
    console_messages: list[str] = []
    person.on("console", lambda message: console_messages.append(message.text))
    documents = []
    person.on("request", lambda request: request.is_navigation_request() and documents.append(request))
    id_token = member_of(person).session.id_token
    person.goto(f"{e2e_server.base_url}{NO_ACCESS_PATH}")

    # The test issuer's host does not resolve, so the browser's request for the end-session endpoint
    # (not blocked by the logout page's form-action) is where the loop ends here.
    with person.expect_event("requestfailed", lambda request: request.url.startswith(END_SESSION_URL)):
        person.get_by_role("button", name="Log out").click()

    assert [(request.method, urlsplit(request.url).path) for request in documents] == [
        ("GET", NO_ACCESS_PATH),
        ("POST", LOGOUT_PATH),
        ("POST", END_SESSION_PATH),
    ]
    assert documents[1].response().status == 200
    assert documents[2].failure == ISSUER_NOT_REACHABLE
    assert parse_qs(documents[2].post_data) == {
        "client_id": ["hopin"],
        "post_logout_redirect_uri": [f"https://{urlsplit(e2e_server.base_url).netloc}{LOGIN_PATH}"],
        "id_token_hint": [id_token.get_secret_value()],
    }
    assert [cookie for cookie in person.context.cookies() if cookie["name"].startswith("__Host-")] == []
    assert e2e_server.stores.sessions.load(member_of(person).session.id) is None
    assert [message for message in console_messages if "Content Security Policy" in message] == []
