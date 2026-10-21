from collections.abc import Callable
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import (
    DESKTOP,
    LEGAL_LINKS,
    PHONE,
    PREJOIN_START,
    SMALL_PHONE_LANDSCAPE,
    expect_both_legal_links,
    open_more,
    open_prejoin,
    record_page_errors,
    wait_until_the_prejoin_media_answered,
)

pytestmark = pytest.mark.e2e

SCROLL_PAST_THE_END = 10_000
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
    expect_both_legal_links(open_more(page))
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


@pytest.mark.parametrize("retired_path", ["/call", "/join/anything"])
def test_retired_addresses_answer_not_found(open_person: Callable[..., Page], base_url: str, retired_path: str) -> None:
    page = open_person()

    response = page.goto(f"{base_url}{retired_path}")

    assert response is not None
    assert response.status == 404
