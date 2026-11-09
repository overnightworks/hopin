import json
from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.skat.skat_page import (
    FRAME,
    LINK_FIELD,
    REMOVE_TABLE,
    SHOW_ITEM,
    STRIP_TILES,
    TOASTS,
    expect_no_table,
    expect_table_on_stage,
    report_of,
    show_table,
    table_link,
)
from tests.e2e.support.calls import join, open_call, open_more, press_through_the_prejoin
from tests.e2e.support.media import MEDIA_FLOWING_TIMEOUT_MILLISECONDS
from tests.e2e.support.people import DESKTOP, FORM_FACTORS, PHONE

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.skat.stub_skat import StubSkat
    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

STRIP_BOTTOM_LIMIT_PIXELS = 170
VIDEO_PLAYING = "(video) => video.videoWidth > 0 && !video.paused"
TAG_VIDEOS = "() => { window.taggedVideos = [...document.querySelectorAll('#videoGrid video')]; }"
VIDEOS_ARE_THE_TAGGED_ONES = """() => {
    const now = [...document.querySelectorAll('#videoGrid video')];
    return now.length === window.taggedVideos.length && now.every((video) => window.taggedVideos.includes(video));
}"""
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
    open_person: Callable[..., Page], e2e_server: RunningServer, stub_skat: StubSkat, page_errors: list[str]
) -> None:
    device = DESKTOP
    host, guest, invite_link = open_call(open_person, e2e_server.base_url, page_errors, device)
    show_table(host, table_link(stub_skat))
    expect_table_on_stage(guest, stub_skat)

    guest.goto(invite_link)
    press_through_the_prejoin(guest)

    expect_table_on_stage(guest, stub_skat)


def test_the_calls_end_takes_the_table_from_the_stage(
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
    expect(host.locator(TOASTS)).to_contain_text("That is not a Skat table link.")
    show_table(host, table_link(stub_skat))
    expect_table_on_stage(guest, stub_skat)
    # A More that still offers the item stands for a seat whose attach crossed the other's.
    guest.evaluate("() => { document.querySelector('#skatItem').hidden = false; }")
    show_table(guest, table_link(stub_skat, "f" * 32))

    expect(guest.locator(TOASTS)).to_contain_text("A Skat table is already on the stage.")
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
    assert set(stub_skat.referers) == {None}
    assert not any(path.startswith("/submitted") for path in stub_skat.paths)
    assert host.url.startswith(e2e_server.base_url)
    assert guest.url == guest_url
