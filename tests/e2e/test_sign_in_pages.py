import re
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Page, expect

from hopin.config.constants import AUTH_CALLBACK_PATH, AUTH_START_PATH, LOGIN_PATH, LOGOUT_PATH, NO_ACCESS_PATH
from tests.e2e.support.calls import record_page_errors
from tests.e2e.support.page_assertions import (
    LEGAL_LINKS,
    RECORD_POLICY_VIOLATIONS,
    expect_both_legal_links,
    expect_both_legal_links_in_the_same_tab,
)
from tests.e2e.support.people import DESKTOP, FORM_FACTORS, PHONE, SMALL_PHONE_LANDSCAPE
from tests.support.page_inventory import inventory_of
from tests.support.sign_in_settings import TEST_ISSUER

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

FORGED_CALLBACK = f"{AUTH_CALLBACK_PATH}?state=forged&code=x"
CARD_AND_ACTION_WIDTHS = """(selectors) => {
    const [cardSelector, actionSelector] = selectors;
    const card = document.querySelector(cardSelector);
    const style = getComputedStyle(card);
    const content = card.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    return [document.querySelector(actionSelector).getBoundingClientRect().width, content];
}"""
BUILT_CARD_SELECTORS = ["main", "main .action"]
NO_ACCESS_CARD_SELECTORS = [".login-card", ".login-card .btn"]
SIGN_IN_STATES = [
    pytest.param(LOGIN_PATH, "Log in", id="signed-out"),
    pytest.param(FORGED_CALLBACK, "Try again", id="login-failed"),
]
END_SESSION_PATH = "/realms/test/protocol/openid-connect/logout"
END_SESSION_URL = f"{TEST_ISSUER}/protocol/openid-connect/logout"


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

    card = person.get_by_role("main")
    expect(card.locator(".wordmark")).to_have_text("HopIn")
    expect(card.get_by_role("heading", name="Login failed", exact=True)).to_be_visible()
    expect(person.get_by_role("alert")).to_have_text("Sign-in did not work. Please try again.")
    expect(card.get_by_role("link")).to_have_text(["Try again"])
    expect_both_legal_links_in_the_same_tab(person.get_by_role("navigation"))
    assert person.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_try_again_is_as_wide_as_the_card_on_a_phone_and_on_a_desktop(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str]
) -> None:
    for form_factor in (PHONE, DESKTOP):
        person = open_login_failed(open_person, e2e_server, page_errors, form_factor)
        action, content = person.evaluate(CARD_AND_ACTION_WIDTHS, BUILT_CARD_SELECTORS)
        assert action == pytest.approx(content, abs=1)


@pytest.mark.parametrize(("path", "action"), SIGN_IN_STATES)
def test_the_one_action_starts_a_new_sign_in_at_the_issuer(
    open_person: Callable[..., Page],
    expected_request_failures: list[str],
    e2e_server: RunningServer,
    page_errors: list[str],
    path: str,
    action: str,
) -> None:
    expected_request_failures.append(TEST_ISSUER)
    person = record_page_errors(open_person(signed_in=False, **DESKTOP), page_errors)
    person.goto(f"{e2e_server.base_url}{path}")
    documents = []
    person.on("request", lambda request: request.is_navigation_request() and documents.append(request))

    with person.expect_request(lambda request: urlsplit(request.url).hostname == "issuer.test.invalid"):
        person.get_by_role("link", name=action, exact=True).click()

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
def test_the_no_access_page_shows_its_heading_its_one_action_and_both_legal_links(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    form_factor: dict[str, Any],
) -> None:
    person = open_signed_out_page(open_person, e2e_server, page_errors, NO_ACCESS_PATH, form_factor)

    expect(person).to_have_title("HopIn - No access")
    card = person.locator(".login-card")
    expect(card.locator(".wordmark")).to_have_text("HopIn")
    expect(card.get_by_role("heading", name="No access", exact=True)).to_be_visible()
    expect(card.locator(".btn")).to_have_text(["Log out"])
    expect_both_legal_links(person.locator(".legal-links"))
    assert person.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_signed_out_page_shows_its_heading_its_one_action_and_both_legal_links_in_the_same_tab(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    person = open_signed_out_page(open_person, e2e_server, page_errors, LOGIN_PATH, form_factor)

    expect(person).to_have_title("HopIn")
    card = person.get_by_role("main")
    expect(card.locator(".wordmark")).to_have_text("HopIn")
    expect(card.get_by_role("heading", name="You are logged out.", exact=True)).to_be_visible()
    expect(card.get_by_role("link")).to_have_text(["Log in"])
    expect_both_legal_links_in_the_same_tab(person.get_by_role("navigation"))
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
            CARD_AND_ACTION_WIDTHS, NO_ACCESS_CARD_SELECTORS
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
    expected_request_failures: list[str],
    member_of: Callable[[Page], Any],
    e2e_server: RunningServer,
    page_errors: list[str],
    form_factor: dict[str, Any],
) -> None:
    """The loop stops at the issuer: the test issuer does not resolve, so the return to Signed out is #25 row 4f's."""
    expected_request_failures.append(END_SESSION_URL)
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


@pytest.mark.parametrize("path", [LOGIN_PATH, FORGED_CALLBACK], ids=["signed-out", "login-failed"])
def test_a_built_sign_in_page_runs_no_script_and_carries_no_inline_style_handler_or_refresh(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], path: str
) -> None:
    person = record_page_errors(open_person(signed_in=False, **DESKTOP), page_errors)
    response = person.goto(f"{e2e_server.base_url}{path}")
    assert response is not None
    markup = response.body().decode("utf-8")

    inventory = inventory_of(markup)
    assert inventory.inline_scripts == 0
    assert "<script" not in markup.lower()
    assert inventory.style_elements == 0
    assert inventory.forbidden_attributes == []
    assert inventory.fetched_urls
    assert all(re.match(r"^/[^/]", url) for url in inventory.fetched_urls)
    assert "http-equiv" not in markup
    assert markup.count("<title>") == 1


@pytest.mark.parametrize("path", [LOGIN_PATH, FORGED_CALLBACK], ids=["signed-out", "login-failed"])
def test_a_built_sign_in_page_loads_every_sub_resource_signed_out_without_a_policy_violation(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str], path: str
) -> None:
    person = record_page_errors(open_person(signed_in=False, **DESKTOP), page_errors)
    document_url = f"{e2e_server.base_url}{path}"
    person.on(
        "console",
        lambda message: (
            message.type == "error" and message.location["url"] != document_url and page_errors.append(message.text)
        ),
    )
    person.add_init_script(RECORD_POLICY_VIOLATIONS)
    sub_resources = []
    person.on("response", lambda response: sub_resources.append(response))

    person.goto(f"{e2e_server.base_url}{path}")
    person.wait_for_load_state("networkidle")

    assert any(urlsplit(response.url).path.startswith("/_app/") for response in sub_resources)
    assert {response.status for response in sub_resources if response.request.resource_type != "document"} == {200}
    assert person.evaluate("() => window.policyViolations") == []


def test_login_failed_on_a_small_landscape_phone_reaches_try_again_and_both_legal_links(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str]
) -> None:
    person = open_login_failed(open_person, e2e_server, page_errors, SMALL_PHONE_LANDSCAPE)

    for control in (
        person.get_by_role("link", name="Try again", exact=True),
        *(person.get_by_role("link", name=label, exact=True) for label in LEGAL_LINKS),
    ):
        control.scroll_into_view_if_needed()
        expect(control).to_be_in_viewport()
    assert person.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
