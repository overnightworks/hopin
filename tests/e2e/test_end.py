import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from playwright.sync_api import Error, Locator, Page, Route, WebSocketRoute, expect

from hopin.config.constants import (
    HOST_GRACE_SECONDS,
    ICE_SERVERS_PATH,
    MAX_PARTICIPANTS_PER_ROOM,
    ErrorReason,
    RoomClosedReason,
    SignalType,
)
from tests.e2e.conftest import (
    COUNT_SOCKETS,
    DESKTOP,
    FAIL_MEDIA,
    HOLD_MEDIA,
    ICE_SERVERS_URL_PATTERN,
    INVITE_LINK_PATTERN,
    LIVE_TRACK_COUNT,
    MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    MEDIA_REQUEST_COUNT,
    MEDIA_REQUEST_IS_HELD,
    MORE_TRACKS_THAN,
    PHONE,
    RECORD_MEDIA_TRACKS,
    RELEASE_MEDIA,
    REQUESTED_TRACK_COUNT,
    STOP_SHARING_LABEL,
    TRACKS_WERE_REQUESTED,
    expect_both_legal_links,
    press_start_call,
    record_page_errors,
    share_the_screen,
    start_call,
)

pytestmark = pytest.mark.e2e

CAPACITY = MAX_PARTICIPANTS_PER_ROOM
IN_CALL = "In call"
SEND_END_ROOM = 'async () => (await import("/static/js/signaling.js")).send({ type: "end_room" })'
# startCall continues in the same microtask drain in which the ICE servers' body is read,
# so a later poll that sees the flag also sees whatever that continuation did.
RECORD_ICE_SERVERS_READ = f"""
window.iceServersRead = false;
const nativeReadJson = Response.prototype.json;
Response.prototype.json = async function () {{
    const body = await nativeReadJson.call(this);
    if (new URL(this.url).pathname === {json.dumps(ICE_SERVERS_PATH)}) window.iceServersRead = true;
    return body;
}};
"""
ICE_SERVERS_WERE_READ = "() => window.iceServersRead"
PRESS_LEAVE = "document.getElementById('leaveBtn').click()"
LEFTOVERS = f"() => ({{ liveTracks: ({LIVE_TRACK_COUNT})(), socketsOpened: window.socketsOpened }})"
NOTHING_LEFT = {"liveTracks": 0, "socketsOpened": 0}


@dataclass(frozen=True)
class EndScreenText:
    title: str
    text: str
    buttons: tuple[str, ...]
    pill: str | None = None


LEFT_BY_GUEST = EndScreenText("You left the call", "The others are still talking.", ("Back to start", "Rejoin"))
LEFT_BY_HOST = EndScreenText("You left the call", "The others are still talking.", ("Back to start",))
ENDED_BY_HOST = EndScreenText("Call ended", "The host ended the call for everyone.", ("Back to start",))
HOST_GONE = EndScreenText(
    "Call ended",
    f"The host didn't come back within {HOST_GRACE_SECONDS} seconds, so the call ended for everyone.",
    ("Back to start",),
)
EXPIRED = EndScreenText("This link has expired", "The call is over. Ask for a new link.", ("Back to start",))
LOCKED = EndScreenText(
    "Call is locked",
    "The host isn't letting anyone new in right now. Try again in a moment.",
    ("Retry", "Back to start"),
)
FULL = EndScreenText(
    "Call is full", f"A call has room for {CAPACITY} people.", ("Back to start",), pill=f"{CAPACITY}/{CAPACITY}"
)
REFUSED_JOINS = [
    pytest.param({"reason": ErrorReason.NOT_FOUND.value}, EXPIRED, id="expired"),
    pytest.param({"reason": ErrorReason.FULL.value, "capacity": CAPACITY}, FULL, id="full"),
    pytest.param({"reason": ErrorReason.LOCKED.value}, LOCKED, id="locked"),
]


@dataclass
class HostedCall:
    host: Page
    invite_link: str
    base_url: str


@dataclass
class Call(HostedCall):
    guest: Page


@pytest.fixture(params=[DESKTOP, PHONE], ids=["desktop", "phone"])
def form_factor(request: pytest.FixtureRequest) -> dict[str, Any]:
    return dict(request.param)


def open_recording_person(open_person: Callable[..., Page], form_factor: dict[str, Any]) -> Page:
    person = open_person(**form_factor)
    person.add_init_script(RECORD_MEDIA_TRACKS)
    return person


@pytest.fixture
def hosted_call(open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any]) -> HostedCall:
    host = open_recording_person(open_person, form_factor)
    return HostedCall(host, start_call(host, base_url), base_url)


@pytest.fixture
def call(hosted_call: HostedCall, open_person: Callable[..., Page], form_factor: dict[str, Any]) -> Call:
    guest = open_recording_person(open_person, form_factor)
    guest.goto(hosted_call.invite_link)
    expect(guest.locator("#status")).to_have_text(IN_CALL, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    return Call(hosted_call.host, hosted_call.invite_link, hosted_call.base_url, guest)


def end_screen(person: Page) -> Locator:
    return person.locator("[data-screen=end]")


def expect_end_title(person: Page, expected: EndScreenText) -> None:
    expect(end_screen(person).get_by_role("heading", name=expected.title)).to_be_visible()


def expect_end_screen(person: Page, expected: EndScreenText) -> None:
    screen = end_screen(person)
    expect_end_title(person, expected)
    expect(screen.locator("#endText")).to_have_text(expected.text)
    if expected.pill:
        expect(screen.locator("#endPill")).to_have_text(expected.pill)
    else:
        expect(screen.locator("#endPill")).to_be_hidden()
    expect(screen.get_by_role("button")).to_have_text(list(expected.buttons))
    expect_both_legal_links(screen)
    expect(person.locator("[data-screen]:visible")).to_have_attribute("data-screen", "end")
    expect(screen.locator(".button-primary")).to_have_count(1)
    expect(screen.locator(".button-primary")).to_have_text(expected.buttons[0])
    assert person.evaluate(TRACKS_WERE_REQUESTED)
    assert person.evaluate(LIVE_TRACK_COUNT) == 0


def guest_leaves(call: Call) -> None:
    call.guest.click("#leaveBtn")


def leave_with_confirmation(person: Page) -> None:
    person.click("#leaveBtn")
    person.click("#leaveYes")


def host_leaves(call: Call) -> None:
    leave_with_confirmation(call.host)


def test_guest_who_leaves_sees_the_left_screen_and_no_live_media_track(call: Call) -> None:
    guest_leaves(call)

    expect_end_screen(call.guest, LEFT_BY_GUEST)


def open_person_whose_startup_media_is_held(open_person: Callable[..., Page], form_factor: dict[str, Any]) -> Page:
    person = open_recording_person(open_person, form_factor)
    for script in (HOLD_MEDIA, COUNT_SOCKETS, RECORD_ICE_SERVERS_READ):
        person.add_init_script(script)
    return person


def leave_and_release_the_held_media(person: Page) -> None:
    person.wait_for_function(MEDIA_REQUEST_IS_HELD)
    person.evaluate(PRESS_LEAVE)
    expect_end_title(person, LEFT_BY_GUEST)
    person.evaluate(RELEASE_MEDIA)
    person.wait_for_function(TRACKS_WERE_REQUESTED)


def test_host_who_leaves_while_the_ice_servers_load_opens_no_connection_and_keeps_no_live_track(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any], page_errors: list[str]
) -> None:
    person = record_page_errors(open_recording_person(open_person, form_factor), page_errors)
    for script in (COUNT_SOCKETS, RECORD_ICE_SERVERS_READ):
        person.add_init_script(script)
    held_ice_requests: list[Route] = []
    person.route(ICE_SERVERS_URL_PATTERN, lambda route: held_ice_requests.append(route))
    with person.expect_request(ICE_SERVERS_URL_PATTERN):
        press_start_call(person, base_url)
    person.evaluate(PRESS_LEAVE)
    expect_end_screen(person, LEFT_BY_HOST)

    held_ice_requests.pop().continue_()
    person.wait_for_function(ICE_SERVERS_WERE_READ)

    assert person.evaluate(LEFTOVERS) == NOTHING_LEFT
    expect_end_screen(person, LEFT_BY_HOST)


def test_media_arriving_after_a_guest_left_once_the_ice_servers_loaded_is_stopped_and_opens_no_connection(
    hosted_call: HostedCall, open_person: Callable[..., Page], form_factor: dict[str, Any], page_errors: list[str]
) -> None:
    person = record_page_errors(open_person_whose_startup_media_is_held(open_person, form_factor), page_errors)
    person.goto(hosted_call.invite_link)
    person.wait_for_function(ICE_SERVERS_WERE_READ)

    leave_and_release_the_held_media(person)

    assert person.evaluate(LEFTOVERS) == NOTHING_LEFT
    expect_end_screen(person, LEFT_BY_GUEST)


def test_camera_failing_after_a_guest_left_asks_for_no_microphone(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    invite_link = start_call(record_page_errors(open_person(**DESKTOP), page_errors), base_url)
    person = record_page_errors(open_recording_person(open_person, DESKTOP), page_errors)
    person.add_init_script(HOLD_MEDIA)
    person.goto(invite_link)
    person.wait_for_function(MEDIA_REQUEST_IS_HELD)
    person.evaluate(PRESS_LEAVE)
    expect_end_title(person, LEFT_BY_GUEST)

    person.evaluate(FAIL_MEDIA)

    assert person.evaluate(MEDIA_REQUEST_COUNT) == 1
    expect_end_title(person, LEFT_BY_GUEST)


def start_sharing_the_screen(person: Page, base_url: str) -> None:
    start_call(person, base_url)
    person.evaluate(HOLD_MEDIA)
    person.click("#screenBtn")


def stop_sharing_the_screen(person: Page, base_url: str) -> None:
    start_call(person, base_url)
    share_the_screen(person)
    person.evaluate(HOLD_MEDIA)
    person.click("#screenBtn")


def turn_on_a_camera_after_starting_without_one(person: Page, base_url: str) -> None:
    press_start_call(person, base_url, with_camera=False)
    expect(person.locator("#shareUrl")).to_have_value(INVITE_LINK_PATTERN)
    person.evaluate(HOLD_MEDIA)
    person.click("#videoBtn")


@pytest.mark.parametrize(
    "request_media_in_the_call",
    [start_sharing_the_screen, stop_sharing_the_screen, turn_on_a_camera_after_starting_without_one],
)
def test_media_requested_in_the_call_that_arrives_after_the_host_left_is_stopped_and_changes_nothing(
    open_person: Callable[..., Page], base_url: str, request_media_in_the_call: Callable[[Page, str], None]
) -> None:
    person = open_recording_person(open_person, DESKTOP)
    page_errors: list[Error] = []
    person.on("pageerror", lambda error: page_errors.append(error))
    request_media_in_the_call(person, base_url)
    person.wait_for_function(MEDIA_REQUEST_IS_HELD)
    leave_with_confirmation(person)
    expect_end_title(person, LEFT_BY_HOST)
    status_before_the_media_arrives = person.locator("#status").text_content()
    tracks_before_the_media_arrives = person.evaluate(REQUESTED_TRACK_COUNT)

    person.evaluate(RELEASE_MEDIA)
    person.wait_for_function(MORE_TRACKS_THAN, arg=tracks_before_the_media_arrives)

    expect_end_screen(person, LEFT_BY_HOST)
    assert person.locator("#status").text_content() == status_before_the_media_arrives
    assert page_errors == []


def test_host_who_shared_the_screen_and_leaves_has_no_live_media_track(hosted_call: HostedCall) -> None:
    share_the_screen(hosted_call.host)

    leave_with_confirmation(hosted_call.host)

    expect_end_screen(hosted_call.host, LEFT_BY_HOST)


def test_host_who_presses_share_twice_while_the_picker_is_open_and_leaves_has_no_live_media_track(
    hosted_call: HostedCall,
) -> None:
    host = hosted_call.host
    host.evaluate(HOLD_MEDIA)
    host.click("#screenBtn")
    host.wait_for_function(MEDIA_REQUEST_IS_HELD)
    host.click("#screenBtn")
    host.evaluate(RELEASE_MEDIA)
    expect(host.locator("#screenBtn")).to_have_text(STOP_SHARING_LABEL)

    leave_with_confirmation(host)

    expect_end_screen(host, LEFT_BY_HOST)


def test_guest_who_rejoins_is_back_in_the_call(call: Call) -> None:
    guest_leaves(call)

    end_screen(call.guest).get_by_role("button", name="Rejoin").click()

    expect(call.guest.locator("#status")).to_have_text(IN_CALL, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    expect(end_screen(call.guest)).to_be_hidden()


def test_guest_who_goes_back_to_start_lands_on_the_home_and_back_returns_to_the_call_link(call: Call) -> None:
    guest_leaves(call)

    end_screen(call.guest).get_by_role("button", name="Back to start").click()

    expect(call.guest).to_have_url(re.compile(rf"^{re.escape(call.base_url)}/$"))
    expect(call.guest.locator("[data-screen=home]")).to_be_visible()
    call.guest.go_back()
    expect(call.guest).to_have_url(call.invite_link)
    expect(call.guest.locator("#status")).to_have_text(IN_CALL, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


def test_host_who_leaves_sees_the_left_screen_without_rejoin(call: Call) -> None:
    host_leaves(call)

    expect_end_screen(call.host, LEFT_BY_HOST)


def test_guest_sees_the_call_ended_when_the_host_ends_it_for_everyone(call: Call) -> None:
    call.host.evaluate(SEND_END_ROOM)

    expect_end_screen(call.guest, ENDED_BY_HOST)


def test_guest_who_finds_the_call_locked_can_retry_once_the_host_unlocks(call: Call) -> None:
    guest_leaves(call)
    call.host.click("#lockBtn")
    expect(call.host.locator("#lockIndicator")).to_have_text("Locked")
    call.guest.goto(call.invite_link)
    expect_end_screen(call.guest, LOCKED)

    call.host.click("#lockBtn")
    expect(call.host.locator("#lockIndicator")).to_have_text("")
    end_screen(call.guest).get_by_role("button", name="Retry").click()

    expect(call.guest.locator("#status")).to_have_text(IN_CALL, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


def answer_join_with(person: Page, frame: dict[str, Any]) -> None:
    def answer(socket: WebSocketRoute) -> None:
        socket.on_message(lambda _frame: socket.send(json.dumps(frame)))

    person.route_web_socket("**/ws?*", answer)


@pytest.mark.parametrize(("error", "expected"), REFUSED_JOINS)
def test_refused_join_shows_the_screen_of_its_reason(
    hosted_call: HostedCall,
    open_person: Callable[..., Page],
    form_factor: dict[str, Any],
    error: dict[str, Any],
    expected: EndScreenText,
) -> None:
    person = open_recording_person(open_person, form_factor)
    answer_join_with(person, {"type": SignalType.ERROR.value, **error})

    person.goto(hosted_call.invite_link)

    expect_end_screen(person, expected)


def test_guest_sees_the_grace_seconds_when_the_host_did_not_come_back(
    hosted_call: HostedCall, open_person: Callable[..., Page], form_factor: dict[str, Any]
) -> None:
    person = open_recording_person(open_person, form_factor)
    answer_join_with(
        person,
        {
            "type": SignalType.ROOM_CLOSED.value,
            "reason": RoomClosedReason.HOST_GONE.value,
            "grace_seconds": HOST_GRACE_SECONDS,
        },
    )

    person.goto(hosted_call.invite_link)

    expect_end_screen(person, HOST_GONE)
