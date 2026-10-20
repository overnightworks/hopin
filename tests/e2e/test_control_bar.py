import json
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
    RECORD_PEER_CONNECTIONS,
    SHARE_THE_SCREEN_FROM_A_CANVAS,
    add_init_scripts,
    join,
    press_start_call,
    record_page_errors,
    share_the_screen,
    start_call,
    stop_the_screen_share,
    wait_for_peer_count,
)

pytestmark = pytest.mark.e2e

BAR = "#stage .cbar"
VISIBLE_BAR_BUTTONS = f"{BAR} button:visible"
ROUND_BUTTON_PIXELS = 52
LEAVE_PILL_PIXELS = {"desktop": 72, "phone": 52}
BAR_NAMES = {
    "desktop": ["Mute", "Camera off", "Share screen", "Chat", "Lock call", "Leave"],
    "phone": ["Mute", "Camera off", "Chat", "Lock call", "Leave"],
}
CLIPBOARD_ALLOWED = {"permissions": [*MEDIA_PERMISSIONS, "clipboard-read", "clipboard-write"]}
OTHER_MUTE_MARK = ".tile:not(.self) .name .muted"
OWN_PICTURE_AND_ICON = "#localVideo", "#localPlaceholder"
LOCAL_AUDIO_TRACKS_ENABLED = (
    "() => document.getElementById('localVideo').srcObject.getAudioTracks().map((t) => t.enabled)"
)
BOXES_OF = """
(selector) => [...document.querySelectorAll(selector)]
    .filter((element) => element.getClientRects().length)
    .map((element) => {
        const { left, top, right, bottom } = element.getBoundingClientRect();
        return { left, top, right, bottom };
    })
"""
BAR_BUTTONS = "#stage .cbar button"
SETTLED_TOAST_BOTTOM = """
async (toast) => {
    await Promise.all(toast.getAnimations().map((animation) => animation.finished));
    return toast.getBoundingClientRect().bottom;
}
"""
TOAST_BOTTOM_AT_EVERY_ANIMATION_END = """
(toast) => {
    toast.style.animation = "none";
    toast.getBoundingClientRect();
    toast.style.animation = "";
    const animations = toast.getAnimations();
    const bottoms = [];
    for (const animation of animations) {
        animation.pause();
        for (const time of [0, animation.effect.getComputedTiming().duration]) {
            animation.currentTime = time;
            bottoms.push(toast.getBoundingClientRect().bottom);
        }
    }
    return bottoms;
}
"""
RULES_OF_THE_OLD_STRIP = """
() => [...document.styleSheets].flatMap((sheet) => [...sheet.cssRules])
    .map((rule) => rule.cssText)
    .filter((css) => /\\.controls\\b|btn-active|btn-muted|btn-warning|btn-chat-wrap/.test(css))
"""


def form_factor_name(form_factor: dict[str, Any]) -> str:
    return "phone" if form_factor == PHONE else "desktop"


def open_host(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any], *scripts: str
) -> tuple[Page, str]:
    host = record_page_errors(open_person(**form_factor), page_errors)
    add_init_scripts(host, (RECORD_PEER_CONNECTIONS, *scripts))
    return host, start_call(host, base_url)


def open_call(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any], *scripts: str
) -> tuple[Page, Page]:
    host, invite_link = open_host(open_person, base_url, page_errors, form_factor, *scripts)
    guest = join(open_person, invite_link, form_factor, (RECORD_PEER_CONNECTIONS,), page_errors)
    wait_for_peer_count(host, 1)
    wait_for_peer_count(guest, 1)
    return host, guest


def bar_names(person: Page) -> list[str]:
    return [button.get_attribute("aria-label") or "" for button in person.locator(VISIBLE_BAR_BUTTONS).all()]


def button_named(person: Page, name: str) -> Locator:
    return person.locator(BAR).get_by_role("button", name=name, exact=True)


def pressed(button: Locator, *, value: bool) -> None:
    expect(button).to_have_attribute("aria-pressed", str(value).lower())


def intersects(first: dict[str, float], second: dict[str, float]) -> bool:
    return (
        first["left"] < second["right"]
        and second["left"] < first["right"]
        and first["top"] < second["bottom"]
        and second["top"] < first["bottom"]
    )


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_host_bar_shows_round_buttons_in_order_inside_the_stage_and_no_old_strip(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    name = form_factor_name(form_factor)
    host, _ = open_call(open_person, base_url, page_errors, form_factor)

    expect(host.locator(VISIBLE_BAR_BUTTONS)).to_have_count(len(BAR_NAMES[name]))
    assert bar_names(host) == BAR_NAMES[name]
    stage = host.evaluate(BOXES_OF, "#stage")[0]
    for box in host.evaluate(BOXES_OF, BAR_BUTTONS):
        assert stage["left"] <= box["left"]
        assert box["right"] <= stage["right"]
        assert box["bottom"] <= stage["bottom"]
        assert box["bottom"] - box["top"] == ROUND_BUTTON_PIXELS
    leave = button_named(host, "Leave").bounding_box()
    assert leave is not None
    assert leave["width"] == LEAVE_PILL_PIXELS[name]
    expect(host.locator(".controls")).to_have_count(0)
    assert host.evaluate(RULES_OF_THE_OLD_STRIP) == []


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_guest_bar_has_no_lock_button(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host, guest = open_call(open_person, base_url, page_errors, form_factor)

    expect(button_named(host, "Lock call")).to_be_visible()
    expect(button_named(guest, "Lock call")).to_be_hidden()
    assert "Lock call" not in bar_names(guest)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_mute_keeps_its_name_and_is_pressed_exactly_while_the_microphone_is_off(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host, guest = open_call(open_person, base_url, page_errors, form_factor)
    mute = button_named(host, "Mute")

    mute.click()

    pressed(mute, value=True)
    assert host.evaluate(LOCAL_AUDIO_TRACKS_ENABLED) == [False]
    expect(guest.locator(OTHER_MUTE_MARK)).to_be_visible()

    mute.click()

    pressed(mute, value=False)
    assert host.evaluate(LOCAL_AUDIO_TRACKS_ENABLED) == [True]
    expect(guest.locator(OTHER_MUTE_MARK)).to_be_hidden()


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_camera_off_keeps_its_name_and_is_pressed_exactly_while_the_own_tile_shows_the_icon(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host, _ = open_call(open_person, base_url, page_errors, form_factor)
    camera = button_named(host, "Camera off")
    picture, icon = (host.locator(selector) for selector in OWN_PICTURE_AND_ICON)

    camera.click()

    pressed(camera, value=True)
    expect(icon).to_be_visible()
    expect(picture).to_be_hidden()

    camera.click()

    pressed(camera, value=False)
    expect(picture).to_be_visible()
    expect(icon).to_be_hidden()


def test_share_screen_is_pressed_and_active_exactly_while_sharing(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host, _ = open_call(open_person, base_url, page_errors, DESKTOP, SHARE_THE_SCREEN_FROM_A_CANVAS)
    share = button_named(host, "Share screen")

    share_the_screen(host)

    expect(share).to_have_class("cb active")

    stop_the_screen_share(host)

    expect(share).to_have_class("cb")


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_lock_call_is_pressed_with_the_locked_tag_and_a_guest_finds_the_call_locked(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host, invite_link = open_host(open_person, base_url, page_errors, form_factor)
    lock = button_named(host, "Lock call")

    lock.click()

    pressed(lock, value=True)
    expect(lock).to_have_class("cb active")
    expect(host.locator("#lockIndicator")).to_have_text("Locked")
    latecomer = join(open_person, invite_link, form_factor, page_errors=page_errors)
    expect(latecomer.locator("[data-screen=end]")).to_contain_text("locked", ignore_case=True)

    lock.click()

    pressed(lock, value=False)
    expect(host.locator("#lockIndicator")).to_have_text("")


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_chat_button_shows_the_unread_count_and_clears_it_when_the_panel_opens(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host, guest = open_call(open_person, base_url, page_errors, form_factor)
    chat = button_named(host, "Chat")
    badge = host.locator("#chatBadge")

    guest.click("#chatBtn")
    guest.fill("#chatInput", "hello")
    guest.click("#chatSend")

    expect(badge).to_have_text("1")
    expect(badge).to_be_visible()
    expect(chat).to_have_attribute("aria-expanded", "false")

    chat.click()

    expect(chat).to_have_attribute("aria-expanded", "true")
    expect(chat).to_have_class("cb active")
    expect(badge).to_be_hidden()


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_failed_start_draws_the_device_buttons_off_and_disabled_and_leave_still_works(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host = record_page_errors(open_person(**form_factor), page_errors)
    host.route(
        ICE_SERVERS_URL_PATTERN,
        lambda route: route.fulfill(status=500, content_type="application/json", body=json.dumps({})),
    )
    press_start_call(host, base_url)

    expect(host.locator("#status")).to_contain_text("Could not load ICE servers")
    for selector in ("#muteBtn", "#videoBtn", "#screenBtn"):
        expect(host.locator(selector)).to_be_disabled()
    pressed(button_named(host, "Mute"), value=True)
    pressed(button_named(host, "Camera off"), value=True)
    button_named(host, "Mute").dispatch_event("click")
    pressed(button_named(host, "Mute"), value=True)
    button_named(host, "Leave").click()
    expect(host.locator("[data-screen=call]")).to_be_hidden()


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_toast_ends_above_every_bar_button(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host, _ = open_host(open_person, base_url, page_errors, {**form_factor, **CLIPBOARD_ALLOWED})

    host.click("#waitingCopyBtn")

    toast = host.locator("#toasts .toast")
    expect(toast).to_be_visible()
    toast_bottom = toast.evaluate(SETTLED_TOAST_BOTTOM)
    bar_top = min(box["top"] for box in host.evaluate(BOXES_OF, BAR_BUTTONS))
    assert toast_bottom <= bar_top
    assert max(toast.evaluate(TOAST_BOTTOM_AT_EVERY_ANIMATION_END)) <= bar_top


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_no_tile_overlaps_a_bar_button_with_three_people(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host, invite_link = open_host(open_person, base_url, page_errors, form_factor)
    for _ in range(2):
        join(open_person, invite_link, form_factor, page_errors=page_errors)
    wait_for_peer_count(host, 2)
    expect(host.locator(".tile")).to_have_count(3)

    buttons = host.evaluate(BOXES_OF, BAR_BUTTONS)
    for tile in host.evaluate(BOXES_OF, ".tile"):
        assert not any(intersects(tile, button) for button in buttons)


def test_tab_walks_the_bar_in_visual_order_and_space_toggles_mute(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host, _ = open_call(open_person, base_url, page_errors, DESKTOP)
    host.evaluate("() => document.activeElement.blur()")

    for name in BAR_NAMES["desktop"]:
        host.keyboard.press("Tab")
        expect(button_named(host, name)).to_be_focused()
    for name in reversed(BAR_NAMES["desktop"][:-1]):
        host.keyboard.press("Shift+Tab")
        expect(button_named(host, name)).to_be_focused()
    host.keyboard.press("Space")

    pressed(button_named(host, "Mute"), value=True)
