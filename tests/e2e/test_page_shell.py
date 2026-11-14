"""The home page's static guards, judged on the DOM the browser built from the SvelteKit shell and its layout."""

import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import design
import pytest
from playwright.sync_api import Page, expect

from hopin.config.constants import (
    ACCOUNT_CONSOLE_PATH,
    APP_ASSET_PATH,
    IMMUTABLE_ASSET_DIRECTORY,
    LOGOUT_PATH,
)
from hopin.words import text
from tests.e2e.support.people import DESKTOP
from tests.support.page_inventory import (
    CSS_COMMENT,
    LEGAL_URLS,
    NEW_TAB_LINK_ATTRIBUTES,
    PageInventory,
    inventory_of,
)

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.e2e

HOPIN_STYLESHEET = Path(__file__).parents[2] / "frontend" / "src" / "lib" / "assets" / "hopin.css"
KIT_STYLESHEET = Path(design.__file__).parent / "static" / "design.css"
CSS_CUSTOM_PROPERTY_DEFINITION = re.compile(r"(--[\w-]+)\s*:")
CSS_COLOR_SCHEME_DECLARATION = re.compile(r"(?<![\w-])color-scheme\s*:")
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


def test_the_home_page_links_only_to_the_central_legal_pages_and_the_account_console_in_a_new_tab(
    home_inventory: PageInventory,
) -> None:
    assert {link["href"] for link in home_inventory.links} == {*LEGAL_URLS, ACCOUNT_CONSOLE_PATH}
    legal_links = [link for link in home_inventory.links if link["href"] in LEGAL_URLS]
    console_links = [link for link in home_inventory.links if link["href"] == ACCOUNT_CONSOLE_PATH]
    assert all(link[name] == value for link in legal_links for name, value in NEW_TAB_LINK_ATTRIBUTES.items())
    assert all(
        link["target"] == "_blank" and {"noopener", "noreferrer"} <= set((link["rel"] or "").split())
        for link in console_links
    )


def test_the_home_page_posts_one_same_origin_form_to_log_out_from_the_account_menu(
    home_inventory: PageInventory,
) -> None:
    assert [(form["method"], form["action"]) for form in home_inventory.forms] == [("post", LOGOUT_PATH)]
    assert home_inventory.submit_buttons == 1


def test_the_home_page_links_every_stylesheet_and_icon_from_the_builds_immutable_files(
    home_inventory: PageInventory,
) -> None:
    assert home_inventory.stylesheets
    assert home_inventory.icons
    assert all(
        urlsplit(href).path.startswith(f"{APP_ASSET_PATH}/{IMMUTABLE_ASSET_DIRECTORY}/assets/")
        for href in [*home_inventory.stylesheets, *home_inventory.icons]
    )


def test_hopin_defines_no_token_and_no_color_scheme_the_built_kit_owns(
    open_person: Callable[..., Page], base_url: str
) -> None:
    kit_tokens = set(CSS_CUSTOM_PROPERTY_DEFINITION.findall(CSS_COMMENT.sub("", KIT_STYLESHEET.read_text())))
    hopin = CSS_COMMENT.sub("", HOPIN_STYLESHEET.read_text())
    assert kit_tokens
    assert set(CSS_CUSTOM_PROPERTY_DEFINITION.findall(hopin)) & kit_tokens == set()
    assert CSS_COLOR_SCHEME_DECLARATION.search(hopin) is None


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


FORM_CONTROL_FONTS_AS_COMPUTED = """
() => {
    const family = (tag) => {
        const control = document.body.appendChild(document.createElement(tag));
        return getComputedStyle(control).fontFamily;
    };
    return [getComputedStyle(document.body).fontFamily, family("button"), family("input"), family("textarea")];
}
"""


def test_the_form_controls_render_in_the_shells_body_font(open_person: Callable[..., Page], base_url: str) -> None:
    page = open_person(**DESKTOP)
    page.goto(f"{base_url}/")
    expect(page.locator("[data-screen=home]")).to_be_visible()
    expect(page).to_have_title(text("de", "call.pageTitle"))

    body, *controls = page.evaluate(FORM_CONTROL_FONTS_AS_COMPUTED)

    assert controls == [body] * 3
