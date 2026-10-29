import json
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import ConsoleMessage, Page, Route, expect

from hopin.config.constants import ACCOUNT_PATH, AUTH_START_PATH, LOGIN_PATH, LOGOUT_PATH
from tests.e2e.support.calls import open_prejoin, record_page_errors, start_call, wait_until_the_prejoin_media_answered
from tests.e2e.support.people import DESKTOP, FORM_FACTORS, PHONE
from tests.support.sign_in_settings import TEST_ISSUER

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import SecretStr

    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

ACCOUNT_ROUTE = f"**{ACCOUNT_PATH}"
AVATAR = ".account-button:visible"
MENU = "#accountMenu"
LOG_OUT = f"{MENU} .mi"
MENU_NAME = f"{MENU} .menu-head b"
END_SESSION_URL = f"{TEST_ISSUER}/protocol/openid-connect/logout"
MENU_WIDTH = 260
LONG_USERNAME = "felix-hummert-mit-einem-sehr-langen-namen-der-nicht-in-das-menu-passt"
UNBROKEN_USERNAME = "x" * 64
ACCOUNT_SCRIM = "#accountScrim"
HOME_START = "#homeStartBtn"
PREJOIN_START = "#prejoinStartBtn"
LOAD_ERROR = "Failed to load resource"
MOST_TABS_TO_REACH_A_BUTTON = 20
BOX_FITS_THE_VIEWPORT = """(selector) => {
    const box = document.querySelector(selector).getBoundingClientRect();
    return box.left >= 0 && box.top >= 0 && box.right <= innerWidth && box.bottom <= innerHeight;
}"""
NAME_IS_CUT_OFF_ON_ONE_LINE = """(selector) => {
    const name = document.querySelector(selector);
    const oneLine = 1.6 * parseFloat(getComputedStyle(name).fontSize);
    return name.scrollWidth > name.clientWidth && name.getBoundingClientRect().height < oneLine;
}"""
NAME_ENDS_WITH_AN_ELLIPSIS = (
    "(selector) => getComputedStyle(document.querySelector(selector)).textOverflow === 'ellipsis'"
)
BOTH_COLOR_SCHEMES = pytest.mark.parametrize("color_scheme", ["light", "dark"])


def watch(person: Page, page_errors: list[str], *, load_errors: list[str] | None = None) -> Page:
    """Record page errors and console messages; a failed load of a URL in `load_errors` is expected there."""
    record_page_errors(person, page_errors)

    def note(message: ConsoleMessage) -> None:
        is_load_error = message.type == "error" and message.text.startswith(LOAD_ERROR)
        if load_errors is not None and is_load_error and message.location["url"].endswith(ACCOUNT_PATH):
            load_errors.append(message.location["url"])
        else:
            page_errors.append(f"{message.type}: {message.text}")

    person.on("console", note)
    return person


def open_home(open_person: Callable[..., Page], base_url: str, page_errors: list[str], **options: Any) -> Page:
    person = watch(open_person(**options), page_errors)
    person.goto(f"{base_url}/")
    expect(person.locator(AVATAR)).to_be_visible()
    return person


def tab_back_to(person: Page, selector: str) -> None:
    for _ in range(MOST_TABS_TO_REACH_A_BUTTON):
        person.keyboard.press("Shift+Tab")
        if person.evaluate("selector => document.activeElement.matches(selector)", selector):
            return
    pytest.fail(f"keyboard focus never reached {selector}")


def leave_the_call(host: Page) -> None:
    host.click("#leaveBtn")
    host.click("#leaveYes")
    expect(host.locator("[data-screen=end]")).to_be_visible()


def log_out_from_the_open_menu(person: Page, e2e_server: RunningServer, id_token: SecretStr) -> None:
    documents = []
    person.on("request", lambda request: request.is_navigation_request() and documents.append(request))

    # The test issuer does not resolve, so the loop ends at the browser's request for the end-session URL.
    with person.expect_event("requestfailed", lambda request: request.url.startswith(END_SESSION_URL)):
        person.locator(LOG_OUT).click()

    post, end_session = documents
    assert (post.method, urlsplit(post.url).path) == ("POST", LOGOUT_PATH)
    assert post.response().status == 200
    assert (end_session.method, parse_qs(end_session.post_data)) == (
        "POST",
        {
            "client_id": ["hopin"],
            "post_logout_redirect_uri": [f"https://{urlsplit(e2e_server.base_url).netloc}{LOGIN_PATH}"],
            "id_token_hint": [id_token.get_secret_value()],
        },
    )
    assert [cookie for cookie in person.context.cookies() if cookie["name"].startswith("__Host-")] == []


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
@pytest.mark.parametrize(
    ("username", "initials"),
    [("felix.hummert", "FH"), ("felix", "F"), ("a-b_c", "AB"), ("_.felix.hummert", "FH"), ("@felix.hummert", "FH")],
)
def test_avatar_shows_the_initials_of_the_username(
    open_person: Callable[..., Page],
    base_url: str,
    page_errors: list[str],
    form_factor: dict[str, Any],
    username: str,
    initials: str,
) -> None:
    person = open_home(open_person, base_url, page_errors, username=username, **form_factor)

    expect(person.get_by_role("button", name="Account")).to_have_text(initials)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_avatar_keeps_the_person_icon_when_the_username_has_no_letter_or_digit(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    person = open_home(open_person, base_url, page_errors, username="._-", **form_factor)

    expect(person.locator(MENU_NAME)).to_have_text("._-")
    expect(person.locator(AVATAR).locator("svg")).to_be_visible()
    expect(person.locator(AVATAR)).to_have_text("")


def test_desktop_prejoin_has_the_avatar_and_the_phone_prejoin_keeps_back_instead(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    desktop = watch(open_person(**DESKTOP), page_errors)
    phone = watch(open_person(**PHONE), page_errors)

    for person in (desktop, phone):
        open_prejoin(person, base_url)

    expect(desktop.locator(AVATAR)).to_be_visible()
    expect(phone.locator(AVATAR)).to_have_count(0)
    expect(phone.get_by_role("button", name="Back")).to_be_visible()


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_end_screen_has_the_avatar_beside_its_own_buttons(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host = watch(open_person(**form_factor), page_errors)
    start_call(host, base_url)

    leave_the_call(host)

    expect(host.locator(AVATAR)).to_be_visible()
    expect(host.locator("[data-screen=end] .end-actions").get_by_role("button")).to_have_text(["Back to start"])


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
@BOTH_COLOR_SCHEMES
def test_avatar_opens_a_menu_with_the_username_hopin_member_and_one_log_out(
    open_person: Callable[..., Page],
    base_url: str,
    page_errors: list[str],
    form_factor: dict[str, Any],
    color_scheme: str,
) -> None:
    person = open_home(open_person, base_url, page_errors, username="felix", color_scheme=color_scheme, **form_factor)

    person.locator(AVATAR).click()

    menu = person.locator(MENU)
    expect(menu).to_be_visible()
    expect(person.locator(MENU_NAME)).to_have_text("felix")
    expect(menu).to_contain_text("HopIn member")
    expect(menu.get_by_role("button")).to_have_text(["Log out"])
    expect(person.locator(AVATAR)).to_have_attribute("aria-expanded", "true")
    expect(person.locator(LOG_OUT)).to_be_focused()
    assert person.evaluate(BOX_FITS_THE_VIEWPORT, MENU)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_menu_closes_on_escape_and_on_a_tap_outside_and_gives_the_focus_back_to_the_avatar(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    person = open_home(open_person, base_url, page_errors, **form_factor)
    viewport = form_factor["viewport"]

    for close in (
        lambda: person.keyboard.press("Escape"),
        lambda: person.mouse.click(viewport["width"] / 2, viewport["height"] - 4),
    ):
        person.locator(AVATAR).click()
        expect(person.locator(MENU)).to_be_visible()
        close()
        expect(person.locator(MENU)).to_be_hidden()
        expect(person.locator(AVATAR)).to_have_attribute("aria-expanded", "false")
        expect(person.locator(AVATAR)).to_be_focused()


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_pressing_the_avatar_again_closes_the_open_menu_and_keeps_the_focus_on_it(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    person = open_home(open_person, base_url, page_errors, **form_factor)
    person.locator(AVATAR).click()
    expect(person.locator(MENU)).to_be_visible()

    person.locator(AVATAR).focus()
    person.keyboard.press("Enter")

    expect(person.locator(MENU)).to_be_hidden()
    expect(person.locator(AVATAR)).to_have_attribute("aria-expanded", "false")
    expect(person.locator(AVATAR)).to_be_focused()


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_log_out_from_the_home_menu_ends_the_session_and_leaves_through_the_issuers_end_session(
    open_person: Callable[..., Page],
    member_of: Callable[[Page], Any],
    e2e_server: RunningServer,
    page_errors: list[str],
    form_factor: dict[str, Any],
) -> None:
    person = open_home(open_person, e2e_server.base_url, page_errors, **form_factor)
    person.locator(AVATAR).click()

    log_out_from_the_open_menu(person, e2e_server, member_of(person).session.id_token)

    assert e2e_server.stores.sessions.load(member_of(person).session.id) is None


def test_log_out_from_the_end_screen_menu_ends_the_session_the_same_way(
    open_person: Callable[..., Page],
    member_of: Callable[[Page], Any],
    e2e_server: RunningServer,
    page_errors: list[str],
) -> None:
    host = watch(open_person(**DESKTOP), page_errors)
    start_call(host, e2e_server.base_url)
    leave_the_call(host)
    host.locator(AVATAR).click()

    log_out_from_the_open_menu(host, e2e_server, member_of(host).session.id_token)

    assert e2e_server.stores.sessions.load(member_of(host).session.id) is None


def test_member_whose_name_cannot_be_read_gets_the_person_icon_a_nameless_menu_and_hi_there(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    load_errors: list[str] = []
    person = watch(open_person(**DESKTOP), page_errors, load_errors=load_errors)
    person.route(ACCOUNT_ROUTE, lambda route: route.fulfill(status=500, body=json.dumps({})))
    person.goto(f"{base_url}/")

    person.locator(AVATAR).click()

    expect(person.locator(AVATAR).locator("svg")).to_be_visible()
    expect(person.locator(AVATAR)).to_have_text("")
    expect(person.locator(MENU_NAME)).to_be_hidden()
    expect(person.locator(MENU).get_by_role("button")).to_have_text(["Log out"])
    expect(person.locator("#homeGreeting")).to_have_text("Hi there")
    assert len(load_errors) == 1


def test_session_that_ended_goes_through_the_sign_in_once_when_the_account_answers_401(
    open_person: Callable[..., Page],
    member_of: Callable[[Page], Any],
    e2e_server: RunningServer,
    page_errors: list[str],
) -> None:
    load_errors: list[str] = []
    person = watch(open_person(**DESKTOP), page_errors, load_errors=load_errors)
    sign_in_starts: list[str] = []
    person.on("request", lambda request: sign_in_starts.append(request.url) if AUTH_START_PATH in request.url else None)

    def end_the_session_and_answer_401(route: Route) -> None:
        e2e_server.stores.sessions.delete(member_of(person).session.id)
        route.fulfill(status=401, body=json.dumps({}))

    person.route(ACCOUNT_ROUTE, end_the_session_and_answer_401)

    with person.expect_event("requestfailed"):
        person.goto(f"{e2e_server.base_url}/")

    assert len(sign_in_starts) == 1
    assert len(load_errors) == 1


def test_a_username_that_looks_like_markup_is_shown_as_text_in_the_menu_and_the_avatar(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    person = open_home(open_person, base_url, page_errors, username="<b>x</b>", **DESKTOP)

    person.locator(AVATAR).click()

    expect(person.locator(MENU_NAME)).to_have_text("<b>x</b>")
    assert person.locator(f"{MENU_NAME} b").count() == 0
    expect(person.locator(AVATAR)).to_have_text("B")


def test_a_very_long_username_is_cut_off_with_an_ellipsis_and_the_menu_keeps_its_width(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    person = open_home(open_person, base_url, page_errors, username=LONG_USERNAME, **PHONE)

    person.locator(AVATAR).click()

    assert person.evaluate(NAME_IS_CUT_OFF_ON_ONE_LINE, MENU_NAME)
    assert person.evaluate(NAME_ENDS_WITH_AN_ELLIPSIS, MENU_NAME)
    assert person.locator(MENU).evaluate("menu => menu.offsetWidth") == MENU_WIDTH
    assert person.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_a_very_long_username_without_a_break_wraps_the_home_greeting_and_keeps_the_brand(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    person = open_home(open_person, base_url, page_errors, username=UNBROKEN_USERNAME, **PHONE)

    expect(person.locator("#homeGreeting")).to_contain_text(UNBROKEN_USERNAME)
    assert person.evaluate(BOX_FITS_THE_VIEWPORT, "#homeGreeting")
    expect(person.locator("[data-screen=home] .brand")).to_be_visible()


def test_home_asks_for_the_account_once(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    person = watch(open_person(**DESKTOP), page_errors)
    account_requests: list[str] = []
    person.on("request", lambda request: account_requests.append(request.url) if ACCOUNT_PATH in request.url else None)

    # The page starts after it loaded, so the first account request is awaited rather than assumed.
    with person.expect_response(lambda response: ACCOUNT_PATH in response.url):
        person.goto(f"{base_url}/")
    expect(person.locator("#homeGreeting")).not_to_have_attribute("aria-busy", "true")

    assert len(account_requests) == 1


def test_a_running_call_has_no_avatar_and_no_account_menu(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host = watch(open_person(**PHONE), page_errors)
    start_call(host, base_url)

    expect(host.locator(AVATAR)).to_have_count(0)
    expect(host.locator(MENU)).to_be_hidden()


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_an_open_menu_does_not_follow_a_keyboard_start_to_the_prejoin(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    person = open_home(open_person, base_url, page_errors, **form_factor)
    person.locator(AVATAR).click()
    tab_back_to(person, HOME_START)

    person.keyboard.press("Enter")

    expect(person.locator("[data-screen=prejoin]")).to_be_visible()
    expect(person.locator(MENU)).to_be_hidden()
    expect(person.locator(ACCOUNT_SCRIM)).to_be_hidden()


def test_an_open_menu_does_not_follow_a_keyboard_start_to_the_call_and_the_first_click_acts(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    person = open_home(open_person, base_url, page_errors, **DESKTOP)
    person.click(HOME_START)
    wait_until_the_prejoin_media_answered(person)
    person.locator(AVATAR).click()
    tab_back_to(person, PREJOIN_START)

    person.keyboard.press("Enter")

    expect(person.locator("[data-screen=call]")).to_be_visible()
    expect(person.locator(MENU)).to_be_hidden()
    expect(person.locator(ACCOUNT_SCRIM)).to_be_hidden()
    person.click("#muteBtn")
    expect(person.locator("#muteBtn")).to_have_attribute("aria-pressed", "true")
