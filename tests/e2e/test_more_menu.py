import json
import re
from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.support.calls import (
    join,
    more_legal_links,
    open_call,
    open_more,
    press_start_call,
    record_page_errors,
    toggle_the_lock,
)
from tests.e2e.support.media import ICE_SERVERS_URL_PATTERN
from tests.e2e.support.page_assertions import (
    BOX_OF_SETTLED,
    CHECK_ICON_PATH,
    LEGAL_LINKS,
    TOAST_AND_MENU_BOXES,
    VISIBLE_MORE_ENTRIES,
    expect_both_legal_links,
)
from tests.e2e.support.people import DESKTOP, FORM_FACTORS, PHONE

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.e2e

CALL_SCREEN = "[data-screen=call]"
MORE_MENU = "#moreMenu"
MORE_BUTTON = "#moreBtn"
LOCK_SWITCH = "#lockBtn"
ACTIVE = re.compile(r"\bactive\b")
SHEET_MARGIN_PIXELS = 8
POPOVER_WIDTH_PIXELS = 280
POPOVER_BOTTOM_GAP_PIXELS = 88
CENTRE_TOLERANCE_PIXELS = 2
VIDEO_TAP_HEIGHT_PIXELS = 200
MINIMUM_NON_TEXT_CONTRAST = 3
SHEET_AND_PART_BACKGROUNDS = """(selector) => {
    const style = getComputedStyle(document.querySelector(selector));
    const sheet = getComputedStyle(document.querySelector("#moreMenu"));
    return [sheet.backgroundColor, style.backgroundColor];
}"""
THE_TAG_AT_THE_TOP = "#stage .ctop .left #lockIndicator"


def relative_luminance(rgb_text: str) -> float:
    channels = [int(value) for value in re.findall(r"\d+", rgb_text)[:3]]
    linear = [(c / 255 / 12.92) if c / 255 <= 0.04045 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(first: str, second: str) -> float:
    lighter, darker = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def expect_more_closed(person: Page) -> None:
    expect(person.locator(MORE_MENU)).to_be_hidden()
    expect(person.locator("#overlayScrim")).to_be_hidden()
    expect(person.locator(MORE_BUTTON)).to_have_attribute("aria-expanded", "false")
    expect(person.locator(MORE_BUTTON)).not_to_have_class(ACTIVE)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_call_screen_shows_no_legal_link_and_no_dock_while_more_is_closed(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host, guest, _ = open_call(open_person, base_url, page_errors, form_factor)

    for person in (host, guest):
        expect(person.locator(CALL_SCREEN).get_by_role("link")).to_have_count(0)
        expect(person.locator(".call-dock")).to_have_count(0)
        expect_more_closed(person)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_host_opens_more_as_a_sheet_on_a_phone_and_a_popover_on_a_desktop(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host, _, _ = open_call(open_person, base_url, page_errors, form_factor)

    open_more(host)

    stage = host.evaluate(BOX_OF_SETTLED, "#stage")
    menu = host.evaluate(BOX_OF_SETTLED, MORE_MENU)
    scrim = host.evaluate(BOX_OF_SETTLED, "#overlayScrim")
    assert scrim == stage
    if form_factor == PHONE:
        assert menu["left"] == stage["left"] + SHEET_MARGIN_PIXELS
        assert menu["right"] == stage["right"] - SHEET_MARGIN_PIXELS
        assert menu["bottom"] == stage["bottom"] - SHEET_MARGIN_PIXELS
    else:
        more = host.evaluate(BOX_OF_SETTLED, MORE_BUTTON)
        assert menu["width"] == POPOVER_WIDTH_PIXELS
        assert menu["bottom"] == stage["bottom"] - POPOVER_BOTTOM_GAP_PIXELS
        assert abs((menu["left"] + menu["right"]) / 2 - (more["left"] + more["right"]) / 2) <= CENTRE_TOLERANCE_PIXELS
    expect(host.locator(MORE_BUTTON)).to_have_attribute("aria-expanded", "true")
    expect(host.locator(MORE_BUTTON)).to_have_class("cb active")
    assert host.evaluate(VISIBLE_MORE_ENTRIES) == ["Lock call", *LEGAL_LINKS]
    expect(host.get_by_role("dialog", name="More")).to_be_visible()


@pytest.mark.parametrize("color_scheme", ["light", "dark"])
@pytest.mark.parametrize("selector", [".grab", f"{LOCK_SWITCH} .switch"])
def test_phone_sheet_grab_handle_and_lock_switch_off_track_reach_non_text_contrast(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], color_scheme: str, selector: str
) -> None:
    host, _, _ = open_call(open_person, base_url, page_errors, {**PHONE, "color_scheme": color_scheme})
    open_more(host)
    expect(host.locator(LOCK_SWITCH)).to_have_attribute("aria-checked", "false")

    sheet_background, part_background = host.evaluate(SHEET_AND_PART_BACKGROUNDS, selector)

    assert contrast_ratio(part_background, sheet_background) >= MINIMUM_NON_TEXT_CONTRAST


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_more_holds_both_legal_links_and_a_click_opens_one_in_a_new_tab_while_the_call_goes_on(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host, _, _ = open_call(open_person, base_url, page_errors, form_factor)
    host.context.route("https://hallucinai.de/**", lambda route: route.fulfill(status=200, body="legal"))
    legal_links = more_legal_links(host)

    expect_both_legal_links(legal_links)
    with host.context.expect_page() as opened:
        legal_links.get_by_role("link", name="Datenschutz").click()

    assert opened.value.url == LEGAL_LINKS["Datenschutz"]
    expect(host.locator("#status")).to_have_text("In call")
    expect(host.locator(MORE_MENU)).to_be_visible()


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_lock_call_switch_locks_and_unlocks_with_a_toast_for_the_host_and_the_tag_for_everyone(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host, guest, invite_link = open_call(open_person, base_url, page_errors, form_factor)
    switch = host.get_by_role("switch", name="Lock call")

    toggle_the_lock(host)

    expect(switch).to_have_attribute("aria-checked", "true")
    expect(switch).to_contain_text("No one new can join")
    expect(host.locator(MORE_MENU)).to_be_visible()
    expect(host.locator("#toasts")).to_have_text("Call locked")
    expect(host.locator("#toasts svg path")).to_have_attribute("d", CHECK_ICON_PATH)
    boxes = host.evaluate(TOAST_AND_MENU_BOXES, MORE_MENU)
    assert boxes["toast"]["top"] >= 0
    assert boxes["toast"]["bottom"] <= boxes["menu"]["top"]
    expect(guest.locator("#toasts")).to_be_empty()
    for person in (host, guest):
        tag = person.locator(THE_TAG_AT_THE_TOP)
        expect(tag).to_be_visible()
        expect(tag).to_have_class("tag-glass")
        expect(tag).to_have_text("Locked")
        expect(tag.locator("svg")).to_have_count(1)
    latecomer = join(open_person, invite_link, form_factor, page_errors=page_errors)
    expect(latecomer.locator("[data-screen=end]")).to_contain_text("locked", ignore_case=True)

    toggle_the_lock(host)

    expect(switch).to_have_attribute("aria-checked", "false")
    expect(switch).to_contain_text("New people can join")
    expect(host.locator("#toasts")).to_have_text("Call unlocked")
    for person in (host, guest):
        expect(person.locator(THE_TAG_AT_THE_TOP)).to_be_hidden()


@pytest.mark.parametrize("tapped_button", ["moreBtn", "leaveBtn"])
def test_a_tap_where_a_desktop_bar_button_sits_only_closes_more(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], tapped_button: str
) -> None:
    host, _, _ = open_call(open_person, base_url, page_errors, DESKTOP)
    button = host.locator(f"#{tapped_button}").bounding_box()
    assert button is not None
    open_more(host)

    host.mouse.click(button["x"] + button["width"] / 2, button["y"] + button["height"] / 2)

    expect_more_closed(host)
    expect(host.locator("#leaveConfirm")).to_be_hidden()
    expect(host.locator("#status")).to_have_text("In call")


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_tap_over_the_video_closes_more(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host, _, _ = open_call(open_person, base_url, page_errors, form_factor)
    open_more(host)

    host.mouse.click(form_factor["viewport"]["width"] / 2, VIDEO_TAP_HEIGHT_PIXELS)

    expect_more_closed(host)


def test_keyboard_opens_more_on_lock_call_toggles_it_and_both_more_and_escape_close_it_with_focus_on_more(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host, _, _ = open_call(open_person, base_url, page_errors, DESKTOP)
    host.evaluate("() => document.activeElement.blur()")
    for _ in range(host.locator("#stage .cbar button:visible").count()):
        if host.evaluate("() => document.activeElement.id") == "moreBtn":
            break
        host.keyboard.press("Tab")

    host.keyboard.press("Enter")

    expect(host.locator(LOCK_SWITCH)).to_be_focused()

    host.keyboard.press("Space")

    expect(host.locator(LOCK_SWITCH)).to_have_attribute("aria-checked", "true")

    host.keyboard.press("Shift+Tab")
    host.keyboard.press("Shift+Tab")

    expect(host.locator(MORE_BUTTON)).to_be_focused()

    host.keyboard.press("Enter")

    expect_more_closed(host)
    expect(host.locator(MORE_BUTTON)).to_be_focused()

    host.keyboard.press("Enter")
    expect(host.locator(LOCK_SWITCH)).to_be_focused()
    host.keyboard.press("Escape")

    expect_more_closed(host)
    expect(host.locator(MORE_BUTTON)).to_be_focused()


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_more_stays_usable_and_the_bar_enabled_after_a_failed_start(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host = record_page_errors(open_person(**form_factor), page_errors)
    host.route(
        ICE_SERVERS_URL_PATTERN,
        lambda route: route.fulfill(status=500, content_type="application/json", body=json.dumps({})),
    )
    press_start_call(host, base_url)

    expect(host.locator("#status")).to_contain_text("Could not load ICE servers")
    expect(host.locator("#stage .cbar")).to_be_enabled()
    expect(host.locator(MORE_BUTTON)).to_be_enabled()
    expect_both_legal_links(more_legal_links(host))
