import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from playwright.sync_api import Locator, Page, WebSocketRoute, expect

from hopin.config.constants import (
    HOST_GRACE_SECONDS,
    MAX_PARTICIPANTS_PER_ROOM,
    ErrorReason,
    RoomClosedReason,
    SignalType,
)
from tests.e2e.conftest import (
    DESKTOP,
    MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    PHONE,
    expect_both_legal_links,
    press_start_call,
    start_call,
)

pytestmark = pytest.mark.e2e

CAPACITY = MAX_PARTICIPANTS_PER_ROOM
IN_CALL = "In call"
SEND_END_ROOM = 'async () => (await import("/static/js/signaling.js")).send({ type: "end_room" })'
RECORD_MEDIA_TRACKS = """
window.requestedTracks = [];
const requestStream = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
navigator.mediaDevices.getUserMedia = async (constraints) => {
    const stream = await requestStream(constraints);
    window.requestedTracks.push(...stream.getTracks());
    return stream;
};
"""
HOLD_MEDIA_UNTIL_RELEASED = """
const requestRecordedStream = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
const mediaHeld = new Promise((release) => { window.releaseMedia = release; });
navigator.mediaDevices.getUserMedia = async (constraints) => {
    await mediaHeld;
    return requestRecordedStream(constraints);
};
"""
TRACKS_WERE_REQUESTED = "() => window.requestedTracks.length > 0"
LIVE_TRACK_COUNT = "() => window.requestedTracks.filter((track) => track.readyState === 'live').length"


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


def expect_end_screen(person: Page, expected: EndScreenText) -> None:
    screen = end_screen(person)
    expect(screen.get_by_role("heading", name=expected.title)).to_be_visible()
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


def host_leaves(call: Call) -> None:
    call.host.click("#leaveBtn")
    call.host.click("#leaveYes")


def test_guest_who_leaves_sees_the_left_screen_and_no_live_media_track(call: Call) -> None:
    guest_leaves(call)

    expect_end_screen(call.guest, LEFT_BY_GUEST)


def test_media_arriving_after_the_person_left_is_stopped_and_opens_no_connection(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any]
) -> None:
    person = open_recording_person(open_person, form_factor)
    person.add_init_script(HOLD_MEDIA_UNTIL_RELEASED)
    sockets_opened: list[str] = []
    person.on("websocket", lambda socket: sockets_opened.append(socket.url))
    press_start_call(person, base_url)

    person.evaluate("document.getElementById('leaveBtn').click()")
    expect(end_screen(person).get_by_role("heading", name=LEFT_BY_GUEST.title)).to_be_visible()
    person.evaluate("window.releaseMedia()")
    person.wait_for_function(TRACKS_WERE_REQUESTED)

    person.wait_for_function("() => window.requestedTracks.every((track) => track.readyState === 'ended')")
    assert sockets_opened == []
    expect(end_screen(person).get_by_role("heading", name=LEFT_BY_GUEST.title)).to_be_visible()


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
