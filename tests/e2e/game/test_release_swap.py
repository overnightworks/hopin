import json
from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import Page, expect

from hopin.words import text
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
WATCH_THE_VERSION_CHECK = """
() => {
    window.reloadStarted = false;
    window.addEventListener("beforeunload", () => { window.reloadStarted = true; });
    let settle;
    window.versionCheckSettled = new Promise((resolve) => { settle = resolve; });
    const fetchFromTheBrowser = window.fetch;
    window.fetch = async (...request) => {
        const response = await fetchFromTheBrowser(...request);
        if (response.url.endsWith("/_app/version.json")) {
            // Reading the body and yielding one task lets the check's own continuation run, and reload() fires
            // beforeunload synchronously, so a reload it decided on has started by the time this settles.
            response.clone().text().then(() => setTimeout(settle));
        }
        return response;
    };
}
"""
HAS_STARTED_A_RELOAD = "() => window.reloadStarted === true"
SAVED_KEYS_BEYOND_SVELTEKIT = """
() => [...Object.keys(localStorage), ...Object.keys(sessionStorage)].filter((key) => !key.startsWith("sveltekit:"))
"""
ADD_ON_TOAST = text("de", "call.addOnLoadFailedToast")


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


def wait_for_the_reload(page: Page) -> None:
    """The reload lands on whatever route the tab had reached, so only the new document is asserted, not a screen."""
    page.wait_for_function(IS_A_NEW_PAGE)
    page.wait_for_load_state("load")


def test_a_tab_from_before_a_release_reloads_itself_when_start_a_call_needs_a_chunk_the_new_build_lacks(
    old_page: Page, page_build_without_the_game_chunk: Path
) -> None:
    """CALL-45: A tab from before a release reloads itself when start a call needs a chunk the new build lacks."""
    publish_a_new_version(page_build_without_the_game_chunk)
    press_start_a_call(old_page)

    wait_for_the_reload(old_page)


def test_a_tab_keeps_the_add_on_toast_and_stays_when_the_chunk_is_missing_without_a_new_version(old_page: Page) -> None:
    old_page.evaluate(WATCH_THE_VERSION_CHECK)
    press_start_a_call(old_page)

    old_page.evaluate("() => window.versionCheckSettled")
    expect(old_page.get_by_text(ADD_ON_TOAST)).to_be_visible()
    assert not old_page.evaluate(HAS_STARTED_A_RELOAD)
    assert old_page.evaluate(IS_THE_OLD_PAGE)


def test_the_reload_after_a_release_saves_nothing_in_the_browser_beyond_sveltekits_own_keys(
    old_page: Page, page_build_without_the_game_chunk: Path
) -> None:
    publish_a_new_version(page_build_without_the_game_chunk)
    press_start_a_call(old_page)
    wait_for_the_reload(old_page)

    assert old_page.evaluate(SAVED_KEYS_BEYOND_SVELTEKIT) == []
