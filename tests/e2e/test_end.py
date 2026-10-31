import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Error, Locator, Page, Route, WebSocketRoute, expect

from hopin.config.constants import (
    HOST_GRACE_SECONDS,
    ICE_SERVERS_PATH,
    MAX_LIVE_SEATS_PER_ACCOUNT,
    MAX_PARTICIPANTS_PER_ROOM,
    WEBSOCKET_PATH,
    ErrorReason,
    RoomClosedReason,
    SignalType,
)
from tests.e2e.support.calls import (
    add_init_scripts,
    call_link_of,
    join_by_link,
    open_prejoin_by_link,
    press_start_call,
    press_through_the_prejoin,
    record_page_errors,
    start_call,
    toggle_the_lock,
    wait_until_the_prejoin_media_answered,
)
from tests.e2e.support.media import (
    COUNT_SOCKETS,
    HOLD_MEDIA,
    ICE_SERVERS_URL_PATTERN,
    LIVE_TRACK_COUNT,
    MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    MEDIA_REQUEST_IS_HELD,
    MORE_TRACKS_THAN,
    OTHER_TILE,
    RECORD_MEDIA_TRACKS,
    RELEASE_MEDIA,
    REQUESTED_TRACK_COUNT,
    TRACKS_WERE_REQUESTED,
    share_the_screen,
)
from tests.e2e.support.page_assertions import expect_both_legal_links
from tests.e2e.support.people import DESKTOP, PHONE
from tests.e2e.support.signaling import RECONNECT_DELAYS_MS, FrameInjector

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from tests.e2e.support.browser_coverage import BrowserCoverage

pytestmark = pytest.mark.e2e

CAPACITY = MAX_PARTICIPANTS_PER_ROOM
GOING_AWAY_CLOSE_CODE = 1001
IN_CALL = "In call"
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
TOO_MANY_CALLS = EndScreenText(
    "Too many calls open",
    f"Your account is in {MAX_LIVE_SEATS_PER_ACCOUNT} calls. Leave one to join this one.",
    ("Back to start",),
)
OPEN_SOCKETS_AND_WAIT = f"""async (count) => {{
    const url = location.origin.replace("http", "ws") + {json.dumps(WEBSOCKET_PATH)};
    const sockets = Array.from({{ length: count }}, () => new WebSocket(url));
    await Promise.all(sockets.map((socket) => new Promise((resolve) => socket.addEventListener("open", resolve))));
    window.heldSockets = sockets;
}}"""
REFUSED_JOINS = [
    pytest.param({"reason": ErrorReason.NOT_FOUND.value}, EXPIRED, id="expired"),
    pytest.param({"reason": ErrorReason.FULL.value, "capacity": CAPACITY}, FULL, id="full"),
    pytest.param({"reason": ErrorReason.LOCKED.value}, LOCKED, id="locked"),
]


@dataclass
class HostedCall:
    host: Page
    host_frames: FrameInjector
    invite_link: str
    base_url: str


@dataclass
class Call(HostedCall):
    guest: Page


@pytest.fixture(params=[DESKTOP, PHONE], ids=["desktop", "phone"])
def form_factor(request: pytest.FixtureRequest) -> dict[str, Any]:
    return dict(request.param)


def open_recording_person(
    open_person: Callable[..., Page], form_factor: dict[str, Any], scripts_after_the_recorder: Sequence[str] = ()
) -> Page:
    person = open_person(**form_factor)
    add_init_scripts(person, [RECORD_MEDIA_TRACKS, *scripts_after_the_recorder])
    return person


@pytest.fixture
def hosted_call(open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any]) -> HostedCall:
    host = open_recording_person(open_person, form_factor)
    host_frames = FrameInjector(host)
    return HostedCall(host, host_frames, start_call(host, base_url), base_url)


@pytest.fixture
def call(hosted_call: HostedCall, open_person: Callable[..., Page], form_factor: dict[str, Any]) -> Call:
    guest = open_recording_person(open_person, form_factor)
    join_by_link(guest, hosted_call.invite_link)
    expect(guest.locator("#status")).to_have_text(IN_CALL, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    return Call(hosted_call.host, hosted_call.host_frames, hosted_call.invite_link, hosted_call.base_url, guest)


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
    expect(screen.locator(".end-actions").get_by_role("button")).to_have_text(list(expected.buttons))
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


@pytest.mark.parametrize("is_guest", [False, True], ids=["host", "guest"])
def test_person_who_leaves_while_the_ice_servers_load_opens_no_connection_and_keeps_no_live_track(
    open_person: Callable[..., Page],
    base_url: str,
    form_factor: dict[str, Any],
    page_errors: list[str],
    is_guest: bool,
) -> None:
    invite_link = start_call(record_page_errors(open_person(**DESKTOP), page_errors), base_url) if is_guest else None
    person = record_page_errors(
        open_recording_person(open_person, form_factor, [COUNT_SOCKETS, RECORD_ICE_SERVERS_READ]), page_errors
    )
    held_ice_requests: list[Route] = []
    person.route(ICE_SERVERS_URL_PATTERN, lambda route: held_ice_requests.append(route))
    with person.expect_request(ICE_SERVERS_URL_PATTERN):
        if invite_link is None:
            press_start_call(person, base_url)
        else:
            join_by_link(person, invite_link)
    expected = LEFT_BY_GUEST if is_guest else LEFT_BY_HOST
    person.evaluate(PRESS_LEAVE)
    expect_end_screen(person, expected)

    held_ice_requests.pop().continue_()
    person.wait_for_function(ICE_SERVERS_WERE_READ)

    assert person.evaluate(LEFTOVERS) == NOTHING_LEFT
    expect_end_screen(person, expected)


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
    call_link_of(person)
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


@pytest.mark.parametrize("form_factor", [DESKTOP], indirect=True)
def test_host_who_shared_the_screen_and_leaves_has_no_live_media_track(hosted_call: HostedCall) -> None:
    share_the_screen(hosted_call.host)

    leave_with_confirmation(hosted_call.host)

    expect_end_screen(hosted_call.host, LEFT_BY_HOST)


@pytest.mark.parametrize("form_factor", [DESKTOP], indirect=True)
def test_host_who_presses_share_twice_while_the_picker_is_open_and_leaves_has_no_live_media_track(
    hosted_call: HostedCall,
) -> None:
    host = hosted_call.host
    host.evaluate(HOLD_MEDIA)
    host.click("#screenBtn")
    host.wait_for_function(MEDIA_REQUEST_IS_HELD)
    host.click("#screenBtn")
    host.evaluate(RELEASE_MEDIA)
    expect(host.locator("#screenBtn")).to_have_attribute("aria-pressed", "true")

    leave_with_confirmation(host)

    expect_end_screen(host, LEFT_BY_HOST)


def test_guest_who_rejoins_is_back_in_the_call(call: Call) -> None:
    guest_leaves(call)

    end_screen(call.guest).get_by_role("button", name="Rejoin").click()
    press_through_the_prejoin(call.guest)

    expect(call.guest.locator("#status")).to_have_text(IN_CALL, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    expect(end_screen(call.guest)).to_be_hidden()


def test_guest_who_goes_back_to_start_lands_on_the_home_and_back_returns_to_the_call_link(
    call: Call, browser_coverage: BrowserCoverage
) -> None:
    guest_leaves(call)

    end_screen(call.guest).get_by_role("button", name="Back to start").click()

    expect(call.guest).to_have_url(re.compile(rf"^{re.escape(call.base_url)}/$"))
    expect(call.guest.locator("[data-screen=home]")).to_be_visible()
    with browser_coverage.passing_through(call.guest):
        call.guest.go_back()
    expect(call.guest).to_have_url(call.invite_link)
    expect(call.guest.locator("[data-screen=prejoin]")).to_be_visible()


def test_host_who_cancels_the_leave_dialog_stays_in_the_call_with_the_guest(call: Call) -> None:
    call.host.click("#leaveBtn")
    expect(call.host.locator("#leaveConfirm")).to_be_visible()

    call.host.click("#leaveCancel")

    expect(call.host.locator("#leaveConfirm")).to_be_hidden()
    expect(call.host.locator("#status")).to_have_text(IN_CALL)
    expect(call.host.locator(OTHER_TILE)).to_have_count(1)
    expect(end_screen(call.guest)).to_be_hidden()


def test_host_who_leaves_sees_the_left_screen_without_rejoin(call: Call) -> None:
    host_leaves(call)

    expect_end_screen(call.host, LEFT_BY_HOST)


def test_host_who_reloads_the_call_link_with_the_same_account_leaves_without_rejoin_or_dialog(
    open_person: Callable[..., Page],
    base_url: str,
    form_factor: dict[str, Any],
    page_errors: list[str],
    browser_coverage: BrowserCoverage,
) -> None:
    host = record_page_errors(open_recording_person(open_person, form_factor), page_errors)
    start_call(host, base_url)

    with browser_coverage.passing_through(host):
        host.reload()
    press_through_the_prejoin(host)
    expect(host.locator("#status")).to_have_text(IN_CALL, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    host.click("#leaveBtn")

    expect_end_screen(host, LEFT_BY_HOST)


def test_guest_sees_the_call_ended_when_the_host_ends_it_for_everyone(call: Call) -> None:
    call.host_frames.send({"type": SignalType.END_ROOM})

    expect_end_screen(call.guest, ENDED_BY_HOST)


def test_guest_waiting_on_the_prejoin_of_an_ended_call_finds_the_link_expired_at_join(
    hosted_call: HostedCall, open_person: Callable[..., Page], page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    guest = record_page_errors(open_recording_person(open_person, form_factor), page_errors)
    open_prejoin_by_link(guest, hosted_call.invite_link)
    wait_until_the_prejoin_media_answered(guest)
    hosted_call.host_frames.send({"type": SignalType.END_ROOM})
    expect(hosted_call.host.locator("[data-screen=end]")).to_be_visible()

    press_through_the_prejoin(guest)

    expect_end_screen(guest, EXPIRED)


def test_guest_who_finds_the_call_locked_can_retry_once_the_host_unlocks(
    call: Call, browser_coverage: BrowserCoverage
) -> None:
    guest_leaves(call)
    toggle_the_lock(call.host)
    expect(call.host.locator("#lockIndicator")).to_be_visible()
    with browser_coverage.passing_through(call.guest):
        join_by_link(call.guest, call.invite_link)
    expect_end_screen(call.guest, LOCKED)

    toggle_the_lock(call.host)
    expect(call.host.locator("#lockIndicator")).to_be_hidden()
    end_screen(call.guest).get_by_role("button", name="Retry").click()
    expect(call.guest.locator("[data-screen=prejoin]")).to_be_visible()
    press_through_the_prejoin(call.guest)

    expect(call.guest.locator("#status")).to_have_text(IN_CALL, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


def answer_join_with(person: Page, frame: dict[str, Any]) -> None:
    def answer(socket: WebSocketRoute) -> None:
        socket.on_message(lambda _frame: socket.send(json.dumps(frame)))

    person.route_web_socket("**/ws", answer)


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

    join_by_link(person, hosted_call.invite_link)

    expect_end_screen(person, expected)


def test_guest_whose_request_the_server_answers_with_an_error_that_is_no_refusal_sees_it_and_keeps_the_call(
    hosted_call: HostedCall, open_person: Callable[..., Page], form_factor: dict[str, Any]
) -> None:
    guest = open_recording_person(open_person, form_factor)
    guest_frames = FrameInjector(guest)
    join_by_link(guest, hosted_call.invite_link)
    expect(guest.locator("#status")).to_have_text(IN_CALL, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

    guest_frames.send({"type": SignalType.END_ROOM})

    expect(guest.locator("#status")).to_have_text(re.compile(r"^Error: .+"))
    expect(guest.locator("#status")).to_have_class(re.compile(r"\bstatus-error\b"))
    expect(end_screen(guest)).to_be_hidden()
    expect(hosted_call.host.locator(OTHER_TILE)).to_have_count(1)


def test_person_whose_account_is_in_the_maximum_of_calls_sees_the_refusal_and_does_not_retry(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any], page_errors: list[str]
) -> None:
    """#238 line 1: a person whose account is in the maximum of calls sees the refusal and does not retry."""
    person = record_page_errors(open_recording_person(open_person, form_factor, [COUNT_SOCKETS]), page_errors)
    other_device = open_person(same_account_as=person, **DESKTOP)
    other_device.goto(f"{base_url}/")
    other_device.evaluate(OPEN_SOCKETS_AND_WAIT, MAX_LIVE_SEATS_PER_ACCOUNT)

    press_start_call(person, base_url)

    expect_end_screen(person, TOO_MANY_CALLS)
    assert person.evaluate(LEFTOVERS) == {"liveTracks": 0, "socketsOpened": 1}


def cut_the_socket_on_demand(person: Page) -> Callable[..., None]:
    """Relay the person's sockets; the returned function cuts the latest one, with or without telling the server."""
    sides: list[WebSocketRoute] = []

    def relay(browser_side: WebSocketRoute) -> None:
        server_side = browser_side.connect_to_server()
        sides[:] = [browser_side, server_side]
        browser_side.on_message(lambda frame: server_side.send(frame))
        server_side.on_message(lambda frame: browser_side.send(frame))

    person.route_web_socket("**/ws", relay)

    def cut(*, server_notices: bool) -> None:
        browser_side, server_side = sides
        if not server_notices:
            browser_side.on_close(lambda code, reason: None)
        browser_side.close(code=GOING_AWAY_CLOSE_CODE, reason="cut")
        if server_notices:
            server_side.close()

    return cut


@pytest.mark.parametrize("server_notices", [True, False], ids=["dropped", "taken_over"])
def test_person_whose_reconnect_meets_the_maximum_of_calls_sees_the_refusal_and_does_not_retry(
    hosted_call: HostedCall,
    open_person: Callable[..., Page],
    form_factor: dict[str, Any],
    page_errors: list[str],
    server_notices: bool,
) -> None:
    """#238 line 1: a reconnect refused at the maximum of calls ends with the same refusal screen, without a retry."""
    guest = record_page_errors(open_recording_person(open_person, form_factor, [COUNT_SOCKETS]), page_errors)
    cut = cut_the_socket_on_demand(guest)
    guest.clock.install()
    join_by_link(guest, hosted_call.invite_link)
    expect(guest.locator("#status")).to_have_text(IN_CALL, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    other_device = open_person(same_account_as=guest, **DESKTOP)
    other_device.goto(f"{hosted_call.base_url}/")

    cut(server_notices=server_notices)
    expect(guest.locator("#status")).to_have_text(f"Reconnecting in {RECONNECT_DELAYS_MS[0] // 1000}s...")
    live_elsewhere = MAX_LIVE_SEATS_PER_ACCOUNT if server_notices else MAX_LIVE_SEATS_PER_ACCOUNT - 1
    other_device.evaluate(OPEN_SOCKETS_AND_WAIT, live_elsewhere)
    guest.clock.run_for(RECONNECT_DELAYS_MS[0])

    expect_end_screen(guest, TOO_MANY_CALLS)
    guest.clock.run_for(sum(RECONNECT_DELAYS_MS))
    assert guest.evaluate(LEFTOVERS) == {"liveTracks": 0, "socketsOpened": 2}


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

    join_by_link(person, hosted_call.invite_link)

    expect_end_screen(person, HOST_GONE)
