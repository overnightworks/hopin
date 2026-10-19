from collections.abc import Callable
from typing import Any

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.e2e.conftest import DESKTOP, PHONE, SMALL_PHONE_LANDSCAPE

pytestmark = pytest.mark.e2e

LEGAL_LINKS = {
    "Impressum": "https://hallucinai.de/impressum",
    "Datenschutz": "https://hallucinai.de/datenschutz#hopin",
}
SCROLL_PAST_THE_END = 10_000
FORM_FACTORS = pytest.mark.parametrize("form_factor", [DESKTOP, PHONE], ids=["desktop", "phone"])


def expect_both_legal_links(screen: Locator) -> None:
    for label, url in LEGAL_LINKS.items():
        link = screen.get_by_role("link", name=label)
        expect(link).to_be_visible()
        expect(link).to_have_attribute("href", url)
        expect(link).to_have_attribute("target", "_blank")
        expect(link).to_have_attribute("rel", "noopener")


@FORM_FACTORS
def test_home_shows_start_a_call_and_both_legal_links(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any]
) -> None:
    page = open_person(**form_factor)
    page.goto(f"{base_url}/")

    home = page.locator("[data-screen=home]")
    expect(home.get_by_role("button", name="Start a call")).to_be_visible()
    expect_both_legal_links(home)
    expect(page.locator("header[data-screen=call]")).to_be_hidden()


@FORM_FACTORS
def test_call_screen_reached_from_home_shows_both_legal_links(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any]
) -> None:
    page = open_person(**form_factor)
    page.goto(f"{base_url}/")

    page.click("#homeStartBtn")

    expect_both_legal_links(page.locator("header[data-screen=call]"))
    expect(page.locator("[data-screen=home]")).to_be_hidden()


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
