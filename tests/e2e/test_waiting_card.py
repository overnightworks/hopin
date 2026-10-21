from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import (
    CHECK_ICON_PATH,
    CLIPBOARD_ALLOWED,
    DESKTOP,
    FORM_FACTORS,
    PHONE,
    READ_CLIPBOARD,
    REJECT_SHARE_WITH,
    REMOVE_SHARE,
    STUB_SHARE,
    TOASTS,
    X_ICON_PATH,
    add_init_scripts,
    call_link_of,
    expect_toast,
    join,
    press_start_call,
    record_page_errors,
    start_call,
)

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.e2e

CARD = "#waitingCard"
COPY_BUTTON = "#waitingCopyBtn"
SHARE_BUTTON = "#waitingShareBtn"
LINK_TEXT = "#waitingLink"
TABS_TO_REACH_COPY_LINK_AT_MOST = 20
SEED_CLIPBOARD = "(text) => navigator.clipboard.writeText(text)"
TOAST_MILLISECONDS = 2400
FADE_GRACE_MILLISECONDS = 300
REDUCED_MOTION = {"reduced_motion": "reduce"}
START_TIMING_A_TOAST = """
() => {
    window.toastStoodFor = null;
    let appeared = null;
    new MutationObserver(() => {
        const standing = document.querySelector("#toasts .toast") !== null;
        if (standing && appeared === null) appeared = performance.now();
        if (!standing && appeared !== null && window.toastStoodFor === null) {
            window.toastStoodFor = performance.now() - appeared;
        }
    }).observe(document.getElementById("toasts"), { childList: true });
}
"""
START_WATCHING_A_TOAST_DIM = """
() => {
    window.toastWatch = { wasFullyShown: false, dimmed: false, removed: false };
    const sample = () => {
        const toast = document.querySelector("#toasts .toast");
        if (!toast) {
            window.toastWatch.removed = true;
            return;
        }
        const opacity = Number(getComputedStyle(toast).opacity);
        if (opacity === 1) window.toastWatch.wasFullyShown = true;
        else if (window.toastWatch.wasFullyShown) window.toastWatch.dimmed = true;
        requestAnimationFrame(sample);
    };
    sample();
}
"""


def open_host(
    open_person: Callable[..., Page],
    page_errors: list[str],
    form_factor: dict[str, Any],
    *init_scripts: str,
) -> Page:
    host = record_page_errors(open_person(**form_factor), page_errors)
    add_init_scripts(host, init_scripts)
    return host


def start_as_host(
    open_person: Callable[..., Page],
    base_url: str,
    page_errors: list[str],
    form_factor: dict[str, Any],
    *init_scripts: str,
) -> Page:
    host = open_host(open_person, page_errors, form_factor, *init_scripts)
    start_call(host, base_url)
    return host


def test_alone_the_desktop_host_sees_the_card_with_the_link_and_copy_link_only(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host = start_as_host(open_person, base_url, page_errors, DESKTOP, STUB_SHARE)

    card = host.locator(CARD)
    expect(card).to_be_visible()
    expect(card.get_by_role("heading", name="Waiting for others")).to_be_visible()
    expect(card.get_by_text("Invite people to join this call.")).to_be_visible()
    expect(host.locator(LINK_TEXT)).to_have_text(host.url)
    expect(card.get_by_role("button")).to_have_count(1)
    expect(card.get_by_role("button", name="Copy link")).to_be_visible()
    for removed in ("#shareBox", "#shareUrl", "#copyBtn"):
        expect(host.locator(removed)).to_have_count(0)


def test_alone_the_phone_host_without_web_share_sees_copy_link_alone_and_no_link_text(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host = start_as_host(open_person, base_url, page_errors, PHONE, REMOVE_SHARE)

    expect(host.locator(CARD)).to_be_visible()
    expect(host.locator(LINK_TEXT)).not_to_be_visible()
    expect(host.locator(f"{CARD} button:visible")).to_have_count(1)
    expect(host.locator(COPY_BUTTON)).to_be_visible()


def test_alone_the_phone_host_with_web_share_sees_copy_link_and_share_in_one_row(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host = start_as_host(open_person, base_url, page_errors, PHONE, STUB_SHARE)

    copy_box = host.locator(COPY_BUTTON).bounding_box()
    share_box = host.locator(SHARE_BUTTON).bounding_box()
    assert copy_box is not None
    assert share_box is not None
    assert copy_box["x"] < share_box["x"]
    assert copy_box["y"] == share_box["y"]


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_copy_link_puts_the_call_link_in_the_clipboard_and_says_so_briefly(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host = start_as_host(open_person, base_url, page_errors, {**form_factor, **CLIPBOARD_ALLOWED})

    host.click(COPY_BUTTON)

    expect_toast(host, "Link copied", CHECK_ICON_PATH)
    assert host.evaluate(READ_CLIPBOARD) == call_link_of(host)
    expect(host.locator(TOASTS)).to_be_empty()


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_copy_link_without_clipboard_permission_reports_the_failure_and_throws_nothing(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host = start_as_host(open_person, base_url, page_errors, form_factor)

    host.click(COPY_BUTTON)

    expect_toast(host, "Couldn't copy the link", X_ICON_PATH)


def test_share_hands_the_share_menu_the_call_link_alone(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host = start_as_host(open_person, base_url, page_errors, PHONE, STUB_SHARE)

    host.click(SHARE_BUTTON)

    host.wait_for_function("() => window.sharedArguments.length === 1")
    assert host.evaluate("window.sharedArguments") == [{"url": call_link_of(host)}]
    expect(host.locator(TOASTS)).to_be_empty()


def test_cancelling_the_share_menu_shows_nothing(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host = start_as_host(open_person, base_url, page_errors, PHONE, REJECT_SHARE_WITH % "AbortError")

    host.click(SHARE_BUTTON)

    host.wait_for_function("() => window.shareAttempts === 1")
    expect(host.locator(TOASTS)).to_be_empty()


def test_a_refused_share_reports_the_failure(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host = start_as_host(open_person, base_url, page_errors, PHONE, REJECT_SHARE_WITH % "NotAllowedError")

    host.click(SHARE_BUTTON)

    expect_toast(host, "Couldn't share the link", X_ICON_PATH)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_card_goes_when_a_guest_joins_and_the_guest_never_sees_it(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host = start_as_host(open_person, base_url, page_errors, form_factor)

    guest = join(open_person, call_link_of(host), form_factor, page_errors=page_errors)

    expect(host.locator(CARD)).not_to_be_visible()
    expect(guest.locator(CARD)).not_to_be_visible()


def test_the_card_comes_back_with_the_same_link_when_the_guest_leaves(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host = start_as_host(open_person, base_url, page_errors, DESKTOP)
    call_link = call_link_of(host)
    guest = join(open_person, call_link, page_errors=page_errors)
    expect(host.locator(CARD)).not_to_be_visible()

    guest.close()

    expect(host.locator(CARD)).to_be_visible()
    expect(host.locator(LINK_TEXT)).to_have_text(call_link)


def test_starting_a_call_leaves_the_clipboard_as_it_was(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host = open_host(open_person, page_errors, {**DESKTOP, **CLIPBOARD_ALLOWED})
    host.goto(base_url)
    host.evaluate(SEED_CLIPBOARD, "seed")

    press_start_call(host, base_url)
    expect(host.locator(CARD)).to_be_visible()

    assert host.evaluate(READ_CLIPBOARD) == "seed"


def test_keyboard_reaches_copy_link_and_enter_copies(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host = start_as_host(open_person, base_url, page_errors, {**DESKTOP, **CLIPBOARD_ALLOWED})

    for _ in range(TABS_TO_REACH_COPY_LINK_AT_MOST):
        host.keyboard.press("Tab")
        if host.evaluate("document.activeElement.id") == "waitingCopyBtn":
            break
    expect(host.locator(COPY_BUTTON)).to_be_focused()
    host.keyboard.press("Enter")

    expect_toast(host, "Link copied", CHECK_ICON_PATH)


def test_a_toast_fades_out_before_it_is_removed(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host = start_as_host(open_person, base_url, page_errors, {**DESKTOP, **CLIPBOARD_ALLOWED})
    host.click(COPY_BUTTON)
    expect_toast(host, "Link copied", CHECK_ICON_PATH)

    host.evaluate(START_WATCHING_A_TOAST_DIM)
    host.wait_for_function("() => window.toastWatch.removed")

    assert host.evaluate("window.toastWatch.dimmed")


def test_a_toast_is_gone_within_its_time_under_reduced_motion(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host = start_as_host(open_person, base_url, page_errors, {**DESKTOP, **CLIPBOARD_ALLOWED, **REDUCED_MOTION})
    host.evaluate(START_TIMING_A_TOAST)

    host.click(COPY_BUTTON)

    host.wait_for_function("() => window.toastStoodFor !== null")
    assert host.evaluate("window.toastStoodFor") < TOAST_MILLISECONDS + FADE_GRACE_MILLISECONDS


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_toast_started_just_before_leave_does_not_stand_over_the_end_screen(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host = start_as_host(open_person, base_url, page_errors, {**form_factor, **CLIPBOARD_ALLOWED})
    host.click(COPY_BUTTON)
    expect_toast(host, "Link copied", CHECK_ICON_PATH)

    host.click("#leaveBtn")
    host.click("#leaveYes")

    expect(host.locator("[data-screen=end]")).to_be_visible()
    assert host.evaluate("() => document.getElementById('toasts').childElementCount") == 0
