"""The home page's static guards, judged on the DOM the browser built from the SvelteKit shell and its layout."""

from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import Page, expect

from hopin.config.constants import LOGOUT_PATH, STATIC_PATH
from hopin.web.kit import KIT_PATH
from tests.e2e.support.people import DESKTOP
from tests.support.page_inventory import (
    LEGAL_URLS,
    NEW_TAB_LINK_ATTRIBUTES,
    UI_CONSOLE_CSS,
    PageInventory,
    defined_class_names,
    inventory_of,
)

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.e2e

ANNOUNCER_STYLE_AS_COMPUTED = """
(announcer) => {
    const style = getComputedStyle(announcer);
    return [style.position, style.width, style.height, style.clipPath, style.overflow];
}
"""


@pytest.fixture
def home_inventory(open_person: Callable[..., Page], base_url: str) -> PageInventory:
    page = open_person(**DESKTOP)
    page.goto(f"{base_url}/")
    expect(page.locator("[data-screen=home]")).to_be_visible()
    return inventory_of(page.content())


def test_the_home_page_links_only_to_the_central_legal_pages_in_a_new_tab(home_inventory: PageInventory) -> None:
    assert {link["href"] for link in home_inventory.links} == set(LEGAL_URLS)
    assert all(link[name] == value for link in home_inventory.links for name, value in NEW_TAB_LINK_ATTRIBUTES.items())


def test_the_home_page_posts_one_same_origin_form_to_log_out_from_the_account_menu(
    home_inventory: PageInventory,
) -> None:
    assert [(form["method"], form["action"]) for form in home_inventory.forms] == [("post", LOGOUT_PATH)]
    assert home_inventory.submit_buttons == 1


def test_the_home_page_loads_ui_console_then_the_kit_then_hopin(home_inventory: PageInventory) -> None:
    assert home_inventory.stylesheets == [
        f"{STATIC_PATH}/css/ui-console.css",
        f"{KIT_PATH}/appkit.css",
        f"{STATIC_PATH}/css/hopin.css",
    ]


def test_the_home_page_uses_no_class_name_that_ui_console_defines(home_inventory: PageInventory) -> None:
    vendored = defined_class_names(UI_CONSOLE_CSS.read_text())
    used_in_markup = {name for value in home_inventory.class_values for name in value.split()}

    assert vendored
    assert vendored & used_in_markup == set()


def test_every_screen_holds_exactly_one_legal_nav_with_both_links(home_inventory: PageInventory) -> None:
    assert {"home", "prejoin", "call"} <= home_inventory.screens
    assert set(home_inventory.legal_navs_by_screen) == home_inventory.screens
    for navs in home_inventory.legal_navs_by_screen.values():
        assert navs == [list(LEGAL_URLS)]


def test_the_route_announcer_stays_hidden_by_the_stylesheet_alone(
    open_person: Callable[..., Page], base_url: str
) -> None:
    page = open_person(**DESKTOP)
    page.goto(f"{base_url}/")
    announcer = page.locator("#svelte-announcer")
    expect(announcer).to_be_attached()

    # Chromium applies the style the policy admits, so the stylesheet's rule is judged without it.
    announcer.evaluate("(element) => element.removeAttribute('style')")

    assert announcer.evaluate(ANNOUNCER_STYLE_AS_COMPUTED) == ["absolute", "1px", "1px", "inset(50%)", "hidden"]
