import re
from typing import TYPE_CHECKING, Any

from playwright.sync_api import Locator, Page, expect

from tests.e2e.support.media import (
    LOCAL_VIDEO_PLAYING,
    MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    RECORD_PEER_CONNECTIONS,
    wait_for_peer_count,
)
from tests.e2e.support.people import DESKTOP

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

INVITE_LINK_PATTERN = re.compile(r"/c/\w+")

PREJOIN_MICROPHONE = "#prejoinMicBtn"
PREJOIN_CAMERA = "#prejoinCamBtn"
PREJOIN_START = "#prejoinStartBtn"


def open_prejoin(host: Page, base_url: str) -> None:
    host.goto(f"{base_url}/")
    host.click("#homeStartBtn")


def wait_until_the_prejoin_media_answered(host: Page) -> None:
    expect(host.locator(PREJOIN_START)).to_be_enabled(timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


def press_through_the_prejoin(person: Page, *, with_camera: bool = True) -> None:
    wait_until_the_prejoin_media_answered(person)
    camera = person.locator(PREJOIN_CAMERA)
    if not with_camera and camera.get_attribute("aria-pressed") == "false":
        camera.click()
        expect(camera).to_have_attribute("aria-pressed", "true")
    person.click(PREJOIN_START)


def press_start_call(host: Page, base_url: str, *, with_camera: bool = True) -> None:
    open_prejoin(host, base_url)
    press_through_the_prejoin(host, with_camera=with_camera)


def call_link_of(host: Page) -> str:
    link = host.locator("#waitingLink")
    expect(link).to_have_text(INVITE_LINK_PATTERN)
    return link.text_content() or ""


def start_call(host: Page, base_url: str, *, with_camera: bool = True) -> str:
    press_start_call(host, base_url, with_camera=with_camera)
    call_link = call_link_of(host)
    if with_camera:
        host.wait_for_function(LOCAL_VIDEO_PLAYING, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    return call_link


def record_page_errors(person: Page, page_errors: list[str]) -> Page:
    person.on("pageerror", lambda error: page_errors.append(str(error)))
    return person


def add_init_scripts(person: Page, scripts: Sequence[str]) -> None:
    # Playwright leaves the order of separate init scripts undefined, and a script may build on
    # one before it (the media recorder wraps a refusal stub), so they run as one script, in order.
    person.add_init_script("\n;\n".join(scripts))


def open_prejoin_by_link(person: Page, invite_link: str) -> None:
    person.goto(invite_link)


def join_by_link(person: Page, invite_link: str, *, with_camera: bool = True) -> None:
    open_prejoin_by_link(person, invite_link)
    press_through_the_prejoin(person, with_camera=with_camera)


def join(
    open_person: Callable[..., Page],
    invite_link: str,
    form_factor: dict[str, Any] = DESKTOP,
    init_scripts: Sequence[str] = (),
    page_errors: list[str] | None = None,
) -> Page:
    guest = open_person(**form_factor)
    if page_errors is not None:
        record_page_errors(guest, page_errors)
    add_init_scripts(guest, init_scripts)
    join_by_link(guest, invite_link)
    return guest


def open_host(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any], *scripts: str
) -> tuple[Page, str]:
    host = record_page_errors(open_person(**form_factor), page_errors)
    add_init_scripts(host, (RECORD_PEER_CONNECTIONS, *scripts))
    return host, start_call(host, base_url)


def open_call(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any], *scripts: str
) -> tuple[Page, Page, str]:
    host, invite_link = open_host(open_person, base_url, page_errors, form_factor, *scripts)
    guest = join(open_person, invite_link, form_factor, (RECORD_PEER_CONNECTIONS,), page_errors)
    wait_for_peer_count(host, 1)
    wait_for_peer_count(guest, 1)
    return host, guest, invite_link


def open_more(person: Page) -> Locator:
    menu = person.locator("#moreMenu")
    if not menu.is_visible():
        person.click("#moreBtn")
    expect(menu).to_be_visible()
    return menu


def more_legal_links(person: Page) -> Locator:
    """More's legal links at its foot; the note under Start quiz links the privacy notice too."""
    return open_more(person).get_by_role("navigation", name="Legal")


def toggle_the_lock(host: Page) -> None:
    open_more(host)
    host.click("#lockBtn")
