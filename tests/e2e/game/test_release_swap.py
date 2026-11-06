import json
from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.support.calls import press_through_the_prejoin

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

VERSION_FILE = "_app/version.json"
MARK_THE_OLD_PAGE = "() => { window.openedOnTheOldBuild = true; }"
IS_THE_OLD_PAGE = "() => window.openedOnTheOldBuild === true"
IS_A_NEW_PAGE = "() => window.openedOnTheOldBuild !== true"
SAVED_KEYS_BEYOND_SVELTEKIT = """
() => [...Object.keys(localStorage), ...Object.keys(sessionStorage)].filter((key) => !key.startsWith("sveltekit:"))
"""
ADD_ON_TOAST = "An add-on could not load."


@pytest.fixture
def e2e_server(server_after_a_release: RunningServer) -> RunningServer:
    return server_after_a_release


@pytest.fixture
def old_page(open_person: Callable[..., Page], e2e_server: RunningServer) -> Page:
    """A tab that loaded the home page before the release and has not been reloaded since."""
    page = open_person()
    page.goto(f"{e2e_server.base_url}/")
    expect(page.locator("#homeStartBtn")).to_be_visible()
    page.evaluate(MARK_THE_OLD_PAGE)
    return page


def publish_a_new_version(page_build: Path) -> None:
    (page_build / VERSION_FILE).write_text(json.dumps({"version": "a-newer-build"}))


def press_start_a_call(page: Page) -> None:
    page.click("#homeStartBtn")
    press_through_the_prejoin(page)


def test_a_tab_from_before_a_release_reloads_itself_when_start_a_call_needs_a_chunk_the_new_build_lacks(
    old_page: Page, page_build_without_the_game_chunk: Path
) -> None:
    publish_a_new_version(page_build_without_the_game_chunk)
    press_start_a_call(old_page)

    old_page.wait_for_function(IS_A_NEW_PAGE)
    expect(old_page.locator("#homeStartBtn")).to_be_visible()


def test_a_tab_keeps_the_add_on_toast_and_stays_when_the_chunk_is_missing_without_a_new_version(old_page: Page) -> None:
    with old_page.expect_response(f"**/{VERSION_FILE}"):
        press_start_a_call(old_page)

    expect(old_page.get_by_text(ADD_ON_TOAST)).to_be_visible()
    assert old_page.evaluate(IS_THE_OLD_PAGE)


def test_the_reload_after_a_release_saves_nothing_in_the_browser_beyond_sveltekits_own_keys(
    old_page: Page, page_build_without_the_game_chunk: Path
) -> None:
    publish_a_new_version(page_build_without_the_game_chunk)
    press_start_a_call(old_page)
    old_page.wait_for_function(IS_A_NEW_PAGE)
    expect(old_page.locator("#homeStartBtn")).to_be_visible()

    assert old_page.evaluate(SAVED_KEYS_BEYOND_SVELTEKIT) == []
