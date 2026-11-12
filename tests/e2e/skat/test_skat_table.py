import json
import re
from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.skat.skat_page import (
    FRAME,
    LINK_FIELD,
    REMOVE_TABLE,
    SHOW_ITEM,
    STRIP_TILES,
    SURFACE,
    TOASTS,
    answer_every_ready_with_a_barrier,
    expect_no_table,
    expect_table_on_stage,
    hellos_of,
    report_of,
    show_table,
    table_link,
)
from tests.e2e.support.calls import join, open_call, open_more, press_through_the_prejoin
from tests.e2e.support.dialed_agent import AGENT_TILE, REMOVE
from tests.e2e.support.media import MEDIA_FLOWING_TIMEOUT_MILLISECONDS
from tests.e2e.support.people import DESKTOP, FORM_FACTORS, PHONE
from tests.support.words import text

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.skat.stub_skat import StubSkat
    from tests.e2e.support.browser_coverage import BrowserCoverage
    from tests.e2e.support.dialed_agent import Agent
    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

STRIP_BOTTOM_LIMIT_PIXELS = 170
DRAGGING = re.compile(r"\bdragging\b")
OWN_TILE_IS_STILL = "() => document.getElementById('localContainer').getAnimations().length === 0"
VIDEO_PLAYING = "(video) => video.videoWidth > 0 && !video.paused"
TAG_VIDEOS = "() => { window.taggedVideos = [...document.querySelectorAll('#videoGrid video')]; }"
VIDEOS_ARE_THE_TAGGED_ONES = """() => {
    const now = [...document.querySelectorAll('#videoGrid video')];
    return now.length === window.taggedVideos.length && now.every((video) => window.taggedVideos.includes(video));
}"""
REMOVE_BUTTON_PHONE_PIXELS = 24
FIRST_REMOTE_TIME = "() => document.querySelector('#videoGrid .tile:not(.self) video').currentTime"


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_pasted_link_shows_the_table_on_every_stage_and_to_a_later_joiner_with_every_video_in_the_strip(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    stub_skat: StubSkat,
    page_errors: list[str],
    form_factor: dict[str, Any],
) -> None:
    host, guest, invite_link = open_call(open_person, e2e_server.base_url, page_errors, form_factor)

    show_table(host, table_link(stub_skat))
    late = join(open_person, invite_link, form_factor, page_errors=page_errors)

    for person in (host, guest, late):
        expect_table_on_stage(person, stub_skat)
        frame = person.locator(FRAME)
        expect(frame).to_have_attribute(
            "sandbox", "allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox"
        )
        expect(frame).to_have_attribute("allow", "")
        expect(frame).to_have_attribute("referrerpolicy", "no-referrer")
    for person in (host, guest, late):
        tiles = person.locator(STRIP_TILES)
        expect(tiles).to_have_count(3, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
        frame_box = person.locator(FRAME).bounding_box()
        assert frame_box is not None
        for index in range(3):
            tile_box = tiles.nth(index).bounding_box()
            assert tile_box is not None
            assert tile_box["y"] + tile_box["height"] <= frame_box["y"], "a tile lies below the strip"
            assert tile_box["y"] + tile_box["height"] < STRIP_BOTTOM_LIMIT_PIXELS
    expect(host.locator(REMOVE_TABLE)).to_be_visible()
    expect(guest.locator(REMOVE_TABLE)).to_have_count(0)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_remove_button_lies_below_the_frame_and_both_lie_inside_the_surface(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    stub_skat: StubSkat,
    page_errors: list[str],
    form_factor: dict[str, Any],
) -> None:
    host, _guest, _ = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    show_table(host, table_link(stub_skat))
    expect_table_on_stage(host, stub_skat)
    expect(host.locator(REMOVE_TABLE)).to_be_visible()

    surface = host.locator(SURFACE).bounding_box()
    frame = host.locator(FRAME).bounding_box()
    button = host.locator(REMOVE_TABLE).bounding_box()
    assert surface is not None
    assert frame is not None
    assert button is not None
    assert frame["y"] + frame["height"] <= button["y"], "the button covers the frame"
    assert frame["y"] == pytest.approx(surface["y"], abs=1)
    assert button["y"] + button["height"] == pytest.approx(surface["y"] + surface["height"], abs=1)
    assert frame["x"] == pytest.approx(surface["x"], abs=1)
    assert frame["x"] + frame["width"] == pytest.approx(surface["x"] + surface["width"], abs=1)
    assert surface["x"] <= button["x"]
    assert button["x"] + button["width"] <= surface["x"] + surface["width"]

    if form_factor.get("has_touch"):
        host.locator(REMOVE_TABLE).tap()
    else:
        host.locator(REMOVE_TABLE).click()
    expect_no_table(host)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_frame_fills_the_surface_for_a_guest_who_sees_no_button(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    stub_skat: StubSkat,
    page_errors: list[str],
    form_factor: dict[str, Any],
) -> None:
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    show_table(host, table_link(stub_skat))
    expect_table_on_stage(guest, stub_skat)

    surface = guest.locator(SURFACE).bounding_box()
    frame = guest.locator(FRAME).bounding_box()
    assert surface is not None
    assert frame is not None
    assert frame["y"] == pytest.approx(surface["y"], abs=1)
    assert frame["y"] + frame["height"] == pytest.approx(surface["y"] + surface["height"], abs=1)
    assert frame["x"] == pytest.approx(surface["x"], abs=1)
    assert frame["x"] + frame["width"] == pytest.approx(surface["x"] + surface["width"], abs=1)


def test_the_opener_removes_the_table_and_the_videos_stay_the_same_elements_playing(
    open_person: Callable[..., Page], e2e_server: RunningServer, stub_skat: StubSkat, page_errors: list[str]
) -> None:
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)
    guest.evaluate(TAG_VIDEOS)
    first_time = guest.evaluate(FIRST_REMOTE_TIME)

    show_table(host, table_link(stub_skat))
    expect_table_on_stage(guest, stub_skat)
    host.click(REMOVE_TABLE)

    expect_no_table(guest)
    expect_no_table(host)
    assert guest.evaluate(VIDEOS_ARE_THE_TAGGED_ONES)
    guest.wait_for_function(f"(time) => ({FIRST_REMOTE_TIME})() > time", arg=first_time)
    assert guest.eval_on_selector("#videoGrid .tile:not(.self) video", VIDEO_PLAYING)
    expect(open_more(host).locator(SHOW_ITEM)).to_be_visible()


def test_the_host_removes_a_table_a_guest_brought(
    open_person: Callable[..., Page], e2e_server: RunningServer, stub_skat: StubSkat, page_errors: list[str]
) -> None:
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)

    show_table(guest, table_link(stub_skat))
    expect_table_on_stage(host, stub_skat)
    host.click(REMOVE_TABLE)

    expect_no_table(guest)


def test_anyone_removes_the_table_once_its_opener_has_left(
    open_person: Callable[..., Page], e2e_server: RunningServer, stub_skat: StubSkat, page_errors: list[str]
) -> None:
    device = DESKTOP
    host, opener, invite_link = open_call(open_person, e2e_server.base_url, page_errors, device)
    bystander = join(open_person, invite_link, device, page_errors=page_errors)
    show_table(opener, table_link(stub_skat))
    expect_table_on_stage(bystander, stub_skat)
    expect(bystander.locator(REMOVE_TABLE)).to_have_count(0)

    opener.click("#leaveBtn")

    expect(bystander.locator(REMOVE_TABLE)).to_be_visible(timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    expect_table_on_stage(host, stub_skat)
    bystander.click(REMOVE_TABLE)
    expect_no_table(host)


def test_a_seat_that_reloads_joins_afresh_and_sees_the_table(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    stub_skat: StubSkat,
    page_errors: list[str],
    browser_coverage: BrowserCoverage,
) -> None:
    device = DESKTOP
    host, guest, _invite_link = open_call(open_person, e2e_server.base_url, page_errors, device)
    show_table(host, table_link(stub_skat))
    expect_table_on_stage(guest, stub_skat)

    with browser_coverage.passing_through(guest):
        guest.reload()
    press_through_the_prejoin(guest)

    expect_table_on_stage(guest, stub_skat)


def test_a_guest_who_leaves_takes_the_table_from_their_own_stage(
    open_person: Callable[..., Page], e2e_server: RunningServer, stub_skat: StubSkat, page_errors: list[str]
) -> None:
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)
    show_table(host, table_link(stub_skat))
    expect_table_on_stage(guest, stub_skat)

    guest.click("#leaveBtn")

    expect(guest.locator("[data-screen=end]")).to_be_visible()
    expect_no_table(guest)


def test_a_link_that_is_no_table_of_skat_is_told_and_a_stale_second_attach_is_refused_as_busy(
    open_person: Callable[..., Page], e2e_server: RunningServer, stub_skat: StubSkat, page_errors: list[str]
) -> None:
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)

    show_table(host, "https://elsewhere.example/t/" + "a" * 32)
    expect(host.locator(TOASTS)).to_contain_text(text("de", "skat.badLinkToast"))
    show_table(host, table_link(stub_skat))
    expect_table_on_stage(guest, stub_skat)
    # A More that still offers the item stands for a seat whose attach crossed the other's.
    guest.evaluate("() => { document.querySelector('#skatItem').hidden = false; }")
    show_table(guest, table_link(stub_skat, "f" * 32))

    expect(guest.locator(TOASTS)).to_contain_text(text("de", "skat.busyToast"))
    expect_table_on_stage(host, stub_skat)


def test_the_item_is_not_offered_while_a_table_is_shown(
    open_person: Callable[..., Page], e2e_server: RunningServer, stub_skat: StubSkat, page_errors: list[str]
) -> None:
    host, _guest, _ = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)

    show_table(host, table_link(stub_skat))
    expect_table_on_stage(host, stub_skat)

    expect(open_more(host).locator(SHOW_ITEM)).to_be_hidden()
    expect(host.locator(LINK_FIELD)).to_be_hidden()


def test_the_framed_page_cannot_capture_media_navigate_the_top_or_send_a_referrer(
    open_person: Callable[..., Page], e2e_server: RunningServer, stub_skat: StubSkat, page_errors: list[str]
) -> None:
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)
    dialogs: list[str] = []
    host.on("dialog", lambda dialog: dialogs.append(dialog.message))
    guest_url = guest.url
    requests_before = len(stub_skat.paths), len(stub_skat.referers)

    show_table(host, table_link(stub_skat))
    expect_table_on_stage(host, stub_skat)
    host.frame_locator(FRAME).locator("#capture").click()
    host.frame_locator(FRAME).locator("#submit").click()
    report = report_of(host)
    expect(report).to_contain_text("getDisplayMedia", timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    expect(report).to_contain_text("getUserMedia")

    answers = json.loads(report.text_content() or "{}")
    assert answers["getUserMedia"] == "NotAllowedError"
    assert answers["getDisplayMedia"] == "NotAllowedError"
    assert answers["referrer"] == ""
    assert answers["topNavigation"] == "SecurityError"
    assert answers["alert"] == "returned"
    assert dialogs == []
    assert set(stub_skat.referers[requests_before[1] :]) == {None}
    assert not any(path.startswith("/submitted") for path in stub_skat.paths[requests_before[0] :])
    assert host.url.startswith(e2e_server.base_url)
    assert guest.url == guest_url


HELLO = r'\{"v":1,"type":"hello","attachment":\d+,"attempt":null\}'
BARRIER = r'"barrier"'
HEARD_BARRIER = r'\{"heard":"barrier"\}'


def test_the_call_answers_the_ready_of_the_frame_with_one_hello(
    open_person: Callable[..., Page], e2e_server: RunningServer, stub_skat: StubSkat, page_errors: list[str]
) -> None:
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)

    show_table(host, table_link(stub_skat))

    for person in (host, guest):
        expect(hellos_of(person)).to_have_text(re.compile(rf"\[{HELLO}\]"), timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


@pytest.mark.parametrize("intruder", ["#stranger", "#sandboxed-stranger"])
def test_a_ready_from_another_window_gets_no_hello(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    stub_skat: StubSkat,
    page_errors: list[str],
    intruder: str,
) -> None:
    host, _guest, _ = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)
    show_table(host, table_link(stub_skat))
    expect(hellos_of(host)).to_have_text(re.compile(rf"\[{HELLO}\]"), timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

    answer_every_ready_with_a_barrier(host)

    host.frame_locator(FRAME).locator(intruder).click()
    expect(host.frame_locator(FRAME).locator("body")).to_have_attribute("data-posted", "yes")
    host.frame_locator(FRAME).locator("#again").click()

    # Each barrier follows the call's answer to its ready, so a hello to the intruder would stand before its barrier.
    expect(hellos_of(host)).to_have_text(
        re.compile(rf"^\[{HELLO},{HEARD_BARRIER},{HELLO},{BARRIER}\]$"), timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS
    )


def test_a_ready_for_another_table_gets_no_hello(
    open_person: Callable[..., Page], e2e_server: RunningServer, stub_skat: StubSkat, page_errors: list[str]
) -> None:
    host, _guest, _ = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)
    show_table(host, table_link(stub_skat))
    expect(hellos_of(host)).to_have_text(re.compile(rf"\[{HELLO}\]"), timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

    answer_every_ready_with_a_barrier(host)

    host.frame_locator(FRAME).locator("#wrong-table").click()
    host.frame_locator(FRAME).locator("#again").click()

    # Each barrier follows the call's answer to its ready, so a hello to the wrong table would stand before its barrier.
    expect(hellos_of(host)).to_have_text(
        re.compile(rf"^\[{HELLO},{BARRIER},{HELLO},{BARRIER}\]$"), timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS
    )


def test_a_reloaded_frame_document_is_greeted_again(
    open_person: Callable[..., Page], e2e_server: RunningServer, stub_skat: StubSkat, page_errors: list[str]
) -> None:
    host, _guest, _ = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)
    show_table(host, table_link(stub_skat))
    expect(hellos_of(host)).to_have_text(re.compile(rf"\[{HELLO}\]"), timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    host.frame_locator(FRAME).locator("body").evaluate("(body) => { body.dataset.firstDocument = 'yes'; }")

    host.frame_locator(FRAME).locator("#reload").click()

    expect(host.frame_locator(FRAME).locator("body")).not_to_have_attribute("data-first-document", "yes")
    expect(hellos_of(host)).to_have_text(re.compile(rf"\[{HELLO}\]"))


def test_a_tile_held_in_the_corner_is_released_when_a_table_comes_and_the_pip_is_off(
    open_person: Callable[..., Page], e2e_server: RunningServer, stub_skat: StubSkat, page_errors: list[str]
) -> None:
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, PHONE)
    own_tile = guest.locator("#localContainer")
    guest.wait_for_function(OWN_TILE_IS_STILL)
    box = own_tile.bounding_box()
    assert box is not None
    guest.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    guest.mouse.down()
    expect(own_tile).to_have_class(DRAGGING)

    show_table(host, table_link(stub_skat))
    expect_table_on_stage(guest, stub_skat)

    expect(own_tile).not_to_have_class(DRAGGING)
    guest.mouse.move(box["x"] - 40, box["y"] + 200)
    guest.mouse.up()
    in_strip = own_tile.bounding_box()
    assert in_strip is not None
    assert in_strip["y"] < STRIP_BOTTOM_LIMIT_PIXELS
    assert in_strip["width"] < box["width"]


def test_an_agent_tile_in_a_phone_strip_keeps_a_small_remove_button_that_removes_the_agent(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    stub_skat: StubSkat,
    page_errors: list[str],
    agent_of: Callable[[Page], Agent],
) -> None:
    """CALL-49: The agent's sponsor removes it from its strip tile on a phone while a table shows."""
    host, _guest, _ = open_call(open_person, e2e_server.base_url, page_errors, PHONE)
    agent_of(host).dial_in("Claude")
    show_table(host, table_link(stub_skat))
    expect_table_on_stage(host, stub_skat)

    remove_agent = host.locator(AGENT_TILE + " " + REMOVE)
    expect(remove_agent).to_be_visible()
    box = remove_agent.bounding_box()
    assert box is not None
    assert box["width"] <= REMOVE_BUTTON_PHONE_PIXELS
    assert box["height"] <= REMOVE_BUTTON_PHONE_PIXELS
    remove_agent.tap()

    expect(host.locator(AGENT_TILE)).to_have_count(0)


def test_an_agent_tile_in_a_desktop_strip_keeps_its_full_remove_button(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    stub_skat: StubSkat,
    page_errors: list[str],
    agent_of: Callable[[Page], Agent],
) -> None:
    host, _guest, _ = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)
    agent_of(host).dial_in("Claude")

    show_table(host, table_link(stub_skat))
    expect_table_on_stage(host, stub_skat)

    remove_agent = host.locator(AGENT_TILE + " " + REMOVE)
    expect(remove_agent).to_be_visible()
    box = remove_agent.bounding_box()
    assert box is not None
    assert box["width"] > REMOVE_BUTTON_PHONE_PIXELS


@pytest.mark.parametrize("form_factor", [DESKTOP, PHONE], ids=["desktop", "phone"])
def test_a_strip_tile_shows_no_name_pill_and_an_agent_tile_keeps_its_sparkle(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    stub_skat: StubSkat,
    page_errors: list[str],
    agent_of: Callable[[Page], Agent],
    form_factor: dict[str, Any],
) -> None:
    """CALL-12, CALL-38, CALL-50, CALL-55: While a view fills the stage no strip tile shows a name pill."""
    host, _guest, _ = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    agent_of(host).dial_in("Claude")
    show_table(host, table_link(stub_skat))
    expect_table_on_stage(host, stub_skat)

    expect(host.locator("#videoGrid .tile:not(.self):not(.agent) .name")).to_be_hidden()
    expect(host.locator(AGENT_TILE + " .name")).to_be_hidden()
    expect(host.locator(AGENT_TILE + " .av .icon")).to_be_visible()
