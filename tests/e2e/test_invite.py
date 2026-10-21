import json
import re
from collections.abc import Callable
from typing import Any

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.e2e.conftest import (
    DESKTOP,
    FORM_FACTORS,
    ICE_SERVERS_URL_PATTERN,
    MEDIA_PERMISSIONS,
    PHONE,
    open_call,
    open_more,
    press_start_call,
    record_page_errors,
    start_call,
)
from tests.e2e.test_more_menu import TOAST_AND_MENU_BOXES
from tests.e2e.test_waiting_card import (
    CHECK_ICON_PATH,
    REJECT_SHARE_WITH,
    REMOVE_SHARE,
    STUB_SHARE,
    TOAST_ICON,
    TOASTS,
    X_ICON_PATH,
    expect_toast,
)

pytestmark = pytest.mark.e2e

INVITE_BUTTON = "#inviteBtn"
INVITE_MENU = "#inviteMenu"
INVITE_LINK = "#inviteLink"
OVERLAY_SCRIM = "#overlayScrim"
COPY_BUTTON = "#inviteCopyBtn:visible, #inviteCopyLinkBtn:visible"
SHARE_BUTTON = "#inviteShareBtn"
CARD_COPY_BUTTON = "#waitingCopyBtn"
ACTIVE = re.compile(r"\bactive\b")
CLIPBOARD_ALLOWED = {"permissions": [*MEDIA_PERMISSIONS, "clipboard-read", "clipboard-write"]}
READ_CLIPBOARD = "() => navigator.clipboard.readText()"
SHEET_MARGIN_PIXELS = 8
POPOVER_WIDTH_PIXELS = 340
POPOVER_BOTTOM_GAP_PIXELS = 88
CENTRE_TOLERANCE_PIXELS = 2
DESKTOP_TOAST_OFFSET_PIXELS = 112
PHONE_TOAST_OFFSET_PIXELS = 132
TABS_TO_REACH_AT_MOST = 20
BOX_OF_SETTLED = """
async (selector) => {
    const element = document.querySelector(selector);
    await Promise.all(element.getAnimations().map((animation) => animation.finished));
    const { left, top, right, bottom, width } = element.getBoundingClientRect();
    return { left, top, right, bottom, width };
}
"""


def open_invite(person: Page) -> Locator:
    menu = person.locator(INVITE_MENU)
    if not menu.is_visible():
        person.click(INVITE_BUTTON)
    expect(menu).to_be_visible()
    return menu


def expect_invite_closed(person: Page) -> None:
    expect(person.locator(INVITE_MENU)).to_be_hidden()
    expect(person.locator(OVERLAY_SCRIM)).to_be_hidden()
    expect(person.locator(INVITE_BUTTON)).to_have_attribute("aria-expanded", "false")
    expect(person.locator(INVITE_BUTTON)).not_to_have_class(ACTIVE)


def focus_by_tabbing_to(person: Page, element_id: str) -> None:
    person.evaluate("() => document.activeElement.blur()")
    for _ in range(TABS_TO_REACH_AT_MOST):
        person.keyboard.press("Tab")
        if person.evaluate("() => document.activeElement.id") == element_id:
            return
    pytest.fail(f"Tab never reached #{element_id}")


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_every_bar_has_an_enabled_invite_without_a_tooltip_in_the_call(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host, guest, _ = open_call(open_person, base_url, page_errors, form_factor)

    for person in (host, guest):
        expect(person.get_by_role("button", name="Invite", exact=True)).to_be_enabled()
        expect(person.locator(INVITE_BUTTON)).not_to_have_attribute("title", re.compile(".*"))


def test_host_opens_invite_as_a_sheet_on_a_phone_with_the_link_copy_link_and_share_in_one_row(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host, _, _ = open_call(open_person, base_url, page_errors, PHONE, STUB_SHARE)

    menu = open_invite(host)

    stage = host.evaluate(BOX_OF_SETTLED, "#stage")
    box = host.evaluate(BOX_OF_SETTLED, INVITE_MENU)
    assert box["left"] == stage["left"] + SHEET_MARGIN_PIXELS
    assert box["right"] == stage["right"] - SHEET_MARGIN_PIXELS
    assert box["bottom"] == stage["bottom"] - SHEET_MARGIN_PIXELS
    expect(host.get_by_role("dialog", name="Invite people")).to_be_visible()
    expect(menu.get_by_role("heading", name="Invite people")).to_be_visible()
    expect(host.locator(INVITE_LINK)).to_have_text(host.url)
    copy_box = menu.get_by_role("button", name="Copy link").bounding_box()
    share_box = menu.get_by_role("button", name="Share").bounding_box()
    assert copy_box is not None
    assert share_box is not None
    assert copy_box["x"] < share_box["x"]
    assert copy_box["y"] == share_box["y"]
    expect(host.locator(INVITE_BUTTON)).to_have_attribute("aria-expanded", "true")
    expect(host.locator(INVITE_BUTTON)).to_have_class("cb active")
    expect(menu).not_to_contain_text("Only HopIn members can join.")


def test_host_opens_invite_as_a_popover_on_a_desktop_with_the_link_and_copy_and_no_share(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host, _, _ = open_call(open_person, base_url, page_errors, DESKTOP, STUB_SHARE)

    menu = open_invite(host)

    stage = host.evaluate(BOX_OF_SETTLED, "#stage")
    box = host.evaluate(BOX_OF_SETTLED, INVITE_MENU)
    invite = host.evaluate(BOX_OF_SETTLED, INVITE_BUTTON)
    assert box["width"] == POPOVER_WIDTH_PIXELS
    assert box["bottom"] == stage["bottom"] - POPOVER_BOTTOM_GAP_PIXELS
    assert abs((box["left"] + box["right"]) / 2 - (invite["left"] + invite["right"]) / 2) <= CENTRE_TOLERANCE_PIXELS
    expect(host.locator(INVITE_LINK)).to_have_text(host.url)
    expect(menu.get_by_role("button", name="Copy", exact=True)).to_be_visible()
    expect(menu.get_by_role("button", name="Share")).to_be_hidden()
    expect(host.locator(INVITE_BUTTON)).to_have_attribute("aria-expanded", "true")
    expect(host.locator(INVITE_BUTTON)).to_have_class("cb active")
    expect(menu).not_to_contain_text("Only HopIn members can join.")


def test_a_phone_without_web_share_offers_copy_link_alone(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host, _, _ = open_call(open_person, base_url, page_errors, PHONE, REMOVE_SHARE)

    menu = open_invite(host)

    expect(menu.get_by_role("button", name="Copy link")).to_be_visible()
    expect(menu.get_by_role("button", name="Share")).to_be_hidden()


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_copy_in_invite_puts_the_call_link_in_the_clipboard_and_leaves_invite_open(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host, _, invite_link = open_call(open_person, base_url, page_errors, {**form_factor, **CLIPBOARD_ALLOWED})
    open_invite(host)

    host.click(COPY_BUTTON)

    expect_toast(host, "Link copied", CHECK_ICON_PATH)
    assert host.evaluate(READ_CLIPBOARD) == invite_link
    expect(host.locator(INVITE_MENU)).to_be_visible()


def test_copy_in_invite_without_clipboard_permission_reports_the_failure_and_throws_nothing(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host, _, _ = open_call(open_person, base_url, page_errors, DESKTOP)
    open_invite(host)

    host.click(COPY_BUTTON)

    expect_toast(host, "Couldn't copy the link", X_ICON_PATH)


@pytest.mark.parametrize(
    ("share_script", "toast_text"),
    [
        pytest.param(STUB_SHARE, None, id="resolves"),
        pytest.param(REJECT_SHARE_WITH % "AbortError", None, id="cancelled"),
        pytest.param(REJECT_SHARE_WITH % "NotAllowedError", "Couldn't share the link", id="refused"),
    ],
)
def test_share_in_invite_hands_the_share_menu_the_call_link_alone_and_reports_only_a_refusal(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], share_script: str, toast_text: str | None
) -> None:
    host, _, invite_link = open_call(open_person, base_url, page_errors, PHONE, share_script)
    open_invite(host)

    host.click(SHARE_BUTTON)

    if toast_text is None:
        host.wait_for_function("(window.sharedArguments ?? []).length === 1 || window.shareAttempts === 1")
        expect(host.locator(TOASTS)).to_be_empty()
    else:
        expect_toast(host, toast_text, X_ICON_PATH)
    if share_script == STUB_SHARE:
        assert host.evaluate("window.sharedArguments") == [{"url": invite_link}]


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_guest_invites_with_the_link_of_the_call_the_guest_is_in(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    _, guest, invite_link = open_call(open_person, base_url, page_errors, {**form_factor, **CLIPBOARD_ALLOWED})
    open_invite(guest)

    expect(guest.locator(INVITE_LINK)).to_have_text(invite_link)
    guest.click(COPY_BUTTON)

    expect_toast(guest, "Link copied", CHECK_ICON_PATH)
    assert guest.evaluate(READ_CLIPBOARD) == invite_link


def test_after_a_failed_start_invite_is_disabled_and_opens_nothing_while_more_works(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host = record_page_errors(open_person(**DESKTOP), page_errors)
    host.route(
        ICE_SERVERS_URL_PATTERN,
        lambda route: route.fulfill(status=500, content_type="application/json", body=json.dumps({})),
    )
    press_start_call(host, base_url)

    expect(host.locator("#status")).to_contain_text("Could not load ICE servers")
    expect(host.locator(INVITE_BUTTON)).to_be_disabled()
    host.evaluate("() => document.getElementById('inviteBtn').click()")
    expect_invite_closed(host)
    expect(host.locator("#moreBtn")).to_be_enabled()
    open_more(host)


def close_by_the_x(person: Page) -> None:
    person.click("#inviteCloseBtn")


def close_by_the_scrim(person: Page) -> None:
    leave = person.locator("#leaveBtn").bounding_box()
    assert leave is not None
    person.mouse.click(leave["x"] + leave["width"] / 2, leave["y"] + leave["height"] / 2)


def close_by_escape(person: Page) -> None:
    person.keyboard.press("Escape")


@pytest.mark.parametrize(
    ("form_factor", "close", "returns_focus"),
    [
        pytest.param(DESKTOP, close_by_the_x, True, id="desktop-x"),
        pytest.param(DESKTOP, close_by_the_scrim, False, id="desktop-scrim"),
        pytest.param(DESKTOP, close_by_escape, True, id="desktop-escape"),
        pytest.param(PHONE, close_by_the_x, True, id="phone-x"),
    ],
)
def test_invite_closes_by_its_x_the_scrim_or_escape_and_a_scrim_tap_does_nothing_else(
    open_person: Callable[..., Page],
    base_url: str,
    page_errors: list[str],
    form_factor: dict[str, Any],
    close: Callable[[Page], None],
    returns_focus: bool,
) -> None:
    host, _, _ = open_call(open_person, base_url, page_errors, form_factor)
    open_invite(host)

    close(host)

    expect_invite_closed(host)
    if returns_focus:
        expect(host.locator(INVITE_BUTTON)).to_be_focused()
    expect(host.locator("#leaveConfirm")).to_be_hidden()
    expect(host.locator("#status")).to_have_text("In call")


def test_opening_more_from_an_open_invite_closes_invite_and_shows_one_scrim(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host, _, _ = open_call(open_person, base_url, page_errors, DESKTOP)
    open_invite(host)
    for _ in range(TABS_TO_REACH_AT_MOST):
        host.keyboard.press("Shift+Tab")
        if host.evaluate("() => document.activeElement.id") == "moreBtn":
            break
    expect(host.locator("#moreBtn")).to_be_focused()

    host.keyboard.press("Enter")

    expect(host.locator("#moreMenu")).to_be_visible()
    expect(host.locator(INVITE_MENU)).to_be_hidden()
    expect(host.locator(INVITE_BUTTON)).to_have_attribute("aria-expanded", "false")
    expect(host.locator(f"{OVERLAY_SCRIM}:visible")).to_have_count(1)


def test_keyboard_opens_invite_on_copy_and_enter_copies(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host, _, _ = open_call(open_person, base_url, page_errors, {**DESKTOP, **CLIPBOARD_ALLOWED})
    focus_by_tabbing_to(host, "inviteBtn")

    host.keyboard.press("Enter")

    expect(host.locator("#inviteCopyBtn")).to_be_focused()

    host.keyboard.press("Enter")

    expect_toast(host, "Link copied", CHECK_ICON_PATH)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_toast_stands_above_the_open_invite_and_returns_above_the_bar_once_it_is_closed(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host = record_page_errors(open_person(**form_factor, **CLIPBOARD_ALLOWED), page_errors)
    start_call(host, base_url)
    open_invite(host)

    host.click(COPY_BUTTON)

    expect(host.locator(TOAST_ICON)).to_have_attribute("d", CHECK_ICON_PATH)
    boxes = host.evaluate(TOAST_AND_MENU_BOXES, INVITE_MENU)
    assert boxes["toast"]["bottom"] <= boxes["menu"]["top"]
    assert boxes["toast"]["top"] >= 0
    host.click("#inviteCloseBtn")
    host.click(CARD_COPY_BUTTON)
    expect(host.locator(TOAST_ICON)).to_have_attribute("d", CHECK_ICON_PATH)
    settled = host.evaluate(TOAST_AND_MENU_BOXES, None)
    offset = PHONE_TOAST_OFFSET_PIXELS if form_factor == PHONE else DESKTOP_TOAST_OFFSET_PIXELS
    assert settled["toast"]["bottom"] == pytest.approx(settled["viewport"]["height"] - offset, abs=1)
