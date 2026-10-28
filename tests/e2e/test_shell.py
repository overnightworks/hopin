from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.support.calls import (
    PREJOIN_START,
    more_legal_links,
    open_prejoin,
    record_page_errors,
    wait_until_the_prejoin_media_answered,
)
from tests.e2e.support.page_assertions import LEGAL_LINKS, expect_both_legal_links
from tests.e2e.support.people import DESKTOP, PHONE, SMALL_PHONE_LANDSCAPE

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.e2e

SCROLL_PAST_THE_END = 10_000
SHORT_DESKTOP = {"viewport": {"width": 1280, "height": 400}}
FORM_FACTORS = pytest.mark.parametrize("form_factor", [DESKTOP, PHONE], ids=["desktop", "phone"])


@FORM_FACTORS
def test_home_shows_start_a_call_and_both_legal_links(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any]
) -> None:
    page = open_person(**form_factor)
    page.goto(f"{base_url}/")

    home = page.locator("[data-screen=home]")
    expect(home.get_by_role("button", name="Start a call")).to_be_visible()
    expect_both_legal_links(home)
    expect(page.locator("[data-screen=call]")).to_be_hidden()


@FORM_FACTORS
def test_prejoin_and_call_screen_reached_from_home_each_show_both_legal_links(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any], page_errors: list[str]
) -> None:
    page = record_page_errors(open_person(**form_factor), page_errors)

    open_prejoin(page, base_url)
    expect_both_legal_links(page.locator("[data-screen=prejoin]"))
    expect(page.locator("[data-screen=home]")).to_be_hidden()

    wait_until_the_prejoin_media_answered(page)
    page.click(PREJOIN_START)
    expect(page.locator("[data-screen=call]")).to_be_visible()
    expect_both_legal_links(more_legal_links(page))
    expect(page.locator("[data-screen=prejoin]")).to_be_hidden()


@pytest.mark.parametrize("label", LEGAL_LINKS)
def test_home_on_a_small_landscape_phone_reaches_each_legal_link_without_sideways_scroll(
    open_person: Callable[..., Page], base_url: str, label: str
) -> None:
    page = open_person(**SMALL_PHONE_LANDSCAPE)
    page.goto(f"{base_url}/")

    link = page.locator("[data-screen=home]").get_by_role("link", name=label)
    page.mouse.move(SMALL_PHONE_LANDSCAPE["viewport"]["width"] / 2, SMALL_PHONE_LANDSCAPE["viewport"]["height"] / 2)
    page.mouse.wheel(0, SCROLL_PAST_THE_END)
    expect(link).to_be_in_viewport(ratio=1)
    link.click(trial=True)

    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.evaluate("document.querySelector('[data-screen=home]').scrollWidth <= window.innerWidth")


def test_home_on_a_short_window_does_not_scroll_past_its_own_height(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    page = record_page_errors(open_person(**SHORT_DESKTOP), page_errors)
    page.on("console", lambda message: page_errors.append(f"{message.type}: {message.text}"))
    page.goto(f"{base_url}/")
    expect(page.locator("[data-screen=home]")).to_be_visible()

    assert page.evaluate("document.querySelector('.home').scrollHeight <= document.querySelector('.home').clientHeight")


@pytest.mark.parametrize("retired_path", ["/call", "/join/anything"])
def test_retired_addresses_answer_not_found(open_person: Callable[..., Page], base_url: str, retired_path: str) -> None:
    page = open_person()

    response = page.goto(f"{base_url}{retired_path}")

    assert response is not None
    assert response.status == 404


@pytest.mark.parametrize("path", ["/", "/login"])
def test_page_declares_an_icon_that_loads_and_never_asks_for_favicon_ico(
    open_person: Callable[..., Page], base_url: str, path: str
) -> None:
    page = open_person(signed_in=path == "/", **DESKTOP)
    requested: list[str] = []
    page.on("request", lambda request: requested.append(request.url))

    page.goto(f"{base_url}{path}", wait_until="networkidle")
    icon_url = page.locator("link[rel=icon]").evaluate("link => link.href")
    # Headless Chromium never fetches a tab icon, so the declared address is fetched as the browser would.
    icon = page.request.get(icon_url)

    assert (icon.status, icon.headers["content-type"]) == (200, "image/svg+xml")
    assert f"{base_url}/favicon.ico" not in requested
