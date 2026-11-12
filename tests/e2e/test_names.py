import json
import re
from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import Locator, Page, Route, WebSocketRoute, expect

from hopin.config.constants import SignalType
from tests.e2e.support.calls import join_by_link, record_page_errors, say_in_chat, start_call
from tests.e2e.support.media import MEDIA_FLOWING_TIMEOUT_MILLISECONDS, OTHER_TILE
from tests.e2e.support.people import DESKTOP, PHONE
from tests.support.sign_in_settings import TEST_ISSUER
from tests.support.words import text

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.browser_coverage import BrowserCoverage
    from tests.e2e.support.server import RunningServer
    from tests.support.signed_in import SeededMember

pytestmark = pytest.mark.e2e

HOME_HEADING = "#homeGreeting"
TILE_NAME = f"{OTHER_TILE} .name"
TILE_NAME_TEXT = f"{TILE_NAME} .name-text"
SELF_TILE = "#videoGrid .tile.self"
CHAT_SENDER = "#chatMessages .chat-msg.other .chat-sender"
MARKUP_NAME = "<b>x</b>"
# Long enough to overflow the label at 390 and at 1280 px in a call of two.
LONG_NAME = (
    "Maximiliane-Theodora Friederike Wilhelmine von Hohenwart-Lichtenberg-Ostfriesland zu Sankt-Peter-Ording-Nord"
)
CUT_WITH_ELLIPSIS = "el => el.scrollWidth > el.clientWidth && getComputedStyle(el).textOverflow === 'ellipsis'"
ACCOUNT_ROUTE = "**/api/account"
BOTH_FORM_FACTORS = pytest.mark.parametrize("form_factor", [PHONE, DESKTOP], ids=["390", "1280"])


def box_inside(inner: Locator, outer: Locator) -> bool:
    inner_box = inner.bounding_box()
    outer_box = outer.bounding_box()
    assert inner_box is not None
    assert outer_box is not None
    return (
        outer_box["x"] <= inner_box["x"]
        and inner_box["x"] + inner_box["width"] <= outer_box["x"] + outer_box["width"]
        and outer_box["y"] <= inner_box["y"]
        and inner_box["y"] + inner_box["height"] <= outer_box["y"] + outer_box["height"]
    )


def boxes_overlap(first: Locator, second: Locator) -> bool:
    first_box = first.bounding_box()
    second_box = second.bounding_box()
    assert first_box is not None
    assert second_box is not None
    return (
        first_box["x"] < second_box["x"] + second_box["width"]
        and second_box["x"] < first_box["x"] + first_box["width"]
        and first_box["y"] < second_box["y"] + second_box["height"]
        and second_box["y"] < first_box["y"] + first_box["height"]
    )


def drop_the_socket_once(person: Page) -> tuple[Callable[[], None], list[str]]:
    """Relay the person's socket; the returned function drops it, the list collects the types the server sent."""
    sides: list[WebSocketRoute] = []
    server_frame_types: list[str] = []

    def relay_from_server(browser_side: WebSocketRoute, frame: str | bytes) -> None:
        server_frame_types.append(json.loads(frame)["type"])
        browser_side.send(frame)

    def relay(browser_side: WebSocketRoute) -> None:
        server_side = browser_side.connect_to_server()
        sides[:] = [browser_side, server_side]
        browser_side.on_message(lambda frame: server_side.send(frame))
        server_side.on_message(lambda frame: relay_from_server(browser_side, frame))

    person.route_web_socket("**/ws", relay)

    def drop() -> None:
        for side in sides:
            side.close()

    return drop, server_frame_types


@BOTH_FORM_FACTORS
def test_home_greets_the_member_by_their_username(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict
) -> None:
    person = record_page_errors(open_person(username="felix", **form_factor), page_errors)

    person.goto(f"{base_url}/")

    expect(person.locator(HOME_HEADING)).to_have_text(text("de", "home.greetingNamed", name="felix"))


@BOTH_FORM_FACTORS
def test_each_tile_carries_the_username_of_the_other_person(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict
) -> None:
    """CALL-12: Each tile carries the username of the other person."""
    host = record_page_errors(open_person(username="anna", **form_factor), page_errors)
    invite_link = start_call(host, base_url)
    guest = record_page_errors(open_person(username="ben", **form_factor), page_errors)
    join_by_link(guest, invite_link)

    expect(host.locator(TILE_NAME)).to_have_text("ben", timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    expect(guest.locator(TILE_NAME)).to_have_text("anna", timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    assert "Peer " not in host.locator("#videoGrid").inner_text()


@BOTH_FORM_FACTORS
def test_chat_message_shows_the_username_of_its_sender(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict
) -> None:
    """CALL-12: Chat message shows the username of its sender."""
    host = record_page_errors(open_person(username="anna", **form_factor), page_errors)
    invite_link = start_call(host, base_url)
    guest = record_page_errors(open_person(username="ben", **form_factor), page_errors)
    join_by_link(guest, invite_link)
    expect(host.locator(TILE_NAME)).to_have_text("ben", timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

    say_in_chat(guest, "hello")

    expect(host.locator(CHAT_SENDER)).to_have_text("ben")


def test_tile_of_a_person_who_joins_after_another_left_shows_the_newcomer(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host = record_page_errors(open_person(username="anna", **DESKTOP), page_errors)
    invite_link = start_call(host, base_url)
    leaver = record_page_errors(open_person(username="ben", **DESKTOP), page_errors)
    join_by_link(leaver, invite_link)
    expect(host.locator(TILE_NAME)).to_have_text("ben", timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

    leaver.click("#leaveBtn")
    newcomer = record_page_errors(open_person(username="cleo", **DESKTOP), page_errors)
    join_by_link(newcomer, invite_link)

    expect(host.locator(TILE_NAME)).to_have_text("cleo", timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


def test_tile_keeps_the_username_after_the_other_persons_socket_resumes(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host = record_page_errors(open_person(username="anna", **DESKTOP), page_errors)
    invite_link = start_call(host, base_url)
    guest = record_page_errors(open_person(username="ben", **DESKTOP), page_errors)
    drop_socket, server_frame_types = drop_the_socket_once(guest)
    join_by_link(guest, invite_link)
    expect(host.locator(TILE_NAME)).to_have_text("ben", timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

    with guest.expect_websocket(timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS):
        drop_socket()
    expect(guest.locator("#status")).to_have_text(
        text("de", "call.statusInCall"), timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS
    )
    assert SignalType.RESUMED.value in server_frame_types
    expect(host.locator(TILE_NAME)).to_have_text("ben")


def test_a_username_that_looks_like_markup_is_shown_as_text(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], browser_coverage: BrowserCoverage
) -> None:
    host = record_page_errors(open_person(username=MARKUP_NAME, **DESKTOP), page_errors)
    host.goto(f"{base_url}/")
    expect(host.locator(HOME_HEADING)).to_have_text(text("de", "home.greetingNamed", name=MARKUP_NAME))
    with browser_coverage.passing_through(host):
        invite_link = start_call(host, base_url)
    guest = record_page_errors(open_person(username="ben", **DESKTOP), page_errors)
    join_by_link(guest, invite_link)
    say_in_chat(host, "hello")

    expect(guest.locator(TILE_NAME)).to_have_text(MARKUP_NAME, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    expect(guest.locator(CHAT_SENDER)).to_have_text(MARKUP_NAME)
    assert guest.locator("#videoGrid .name b, #chatMessages b").count() == 0


@BOTH_FORM_FACTORS
def test_a_long_name_ends_with_an_ellipsis_in_a_label_that_clears_the_own_tile(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict
) -> None:
    host = record_page_errors(open_person(username="anna", **form_factor), page_errors)
    invite_link = start_call(host, base_url)
    guest = record_page_errors(open_person(username=LONG_NAME, **form_factor), page_errors)
    join_by_link(guest, invite_link)
    expect(host.locator(TILE_NAME)).to_have_text(LONG_NAME, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

    assert host.locator(TILE_NAME_TEXT).evaluate(CUT_WITH_ELLIPSIS)
    assert box_inside(host.locator(TILE_NAME), host.locator(OTHER_TILE))
    assert not boxes_overlap(host.locator(TILE_NAME), host.locator(SELF_TILE))


def test_home_keeps_the_unnamed_greeting_when_the_account_answer_fails(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    person = record_page_errors(open_person(**DESKTOP), page_errors)
    person.route(ACCOUNT_ROUTE, lambda route: route.fulfill(status=500, body=json.dumps({})))

    person.goto(f"{base_url}/")

    expect(person.locator(HOME_HEADING)).not_to_have_attribute("aria-busy", re.compile(".*"))
    expect(person.locator(HOME_HEADING)).to_have_text(text("de", "home.greetingUnnamed"))


def test_home_whose_session_ended_goes_through_the_sign_in_when_the_account_answers_401(
    open_person: Callable[..., Page],
    expected_request_failures: list[str],
    member_of: Callable[[Page], SeededMember],
    e2e_server: RunningServer,
    page_errors: list[str],
) -> None:
    expected_request_failures.append(TEST_ISSUER)
    person = record_page_errors(open_person(**DESKTOP), page_errors)
    documents: list[str] = []
    person.on("request", lambda request: documents.append(request.url) if request.resource_type == "document" else None)

    def end_the_session_and_answer_401(route: Route) -> None:
        e2e_server.stores.sessions.delete(member_of(person).session.id)
        route.fulfill(status=401, body=json.dumps({}))

    person.route(ACCOUNT_ROUTE, end_the_session_and_answer_401)

    with person.expect_event("requestfailed"):
        person.goto(f"{e2e_server.base_url}/")

    assert documents[-2:] == [f"{e2e_server.base_url}/auth/start?next=%2F", documents[-1]]
    assert documents[-1].startswith(TEST_ISSUER)
