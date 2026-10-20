from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Locator, Page, Request, expect

from hopin.config.constants import ICE_SERVERS_PATH
from tests.e2e.conftest import (
    COUNT_SOCKETS,
    DESKTOP,
    FORM_FACTORS,
    HOLD_MEDIA,
    ICON_ONLY_ON_THE_TILES,
    LOCAL_VIDEO_PLAYING,
    MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    MEDIA_REQUEST_COUNT,
    MEDIA_REQUEST_IS_HELD,
    OTHER_TILE,
    PHONE,
    PREJOIN_CAMERA,
    PREJOIN_MICROPHONE,
    PREJOIN_START,
    RECORD_MEDIA_TRACKS,
    RECORD_PEER_CONNECTIONS,
    REFUSE_THE_CAMERA,
    RELEASE_MEDIA,
    join,
    open_prejoin,
    record_page_errors,
    wait_for_the_picture,
    wait_until_the_prejoin_media_answered,
)

pytestmark = pytest.mark.e2e

IN_CALL = "In call"
BOTH_ON = "Start with camera and mic"
CAMERA_ONLY = "Start with camera only"
MIC_ONLY = "Start with mic only"
BOTH_OFF = "Start with camera and mic off"
MIRRORED = "matrix(-1, 0, 0, 1, 0, 0)"
CAMERA_OFF_SURFACE = "rgb(37, 43, 50)"
PREJOIN_SCREEN = "[data-screen=prejoin]"
VISIBLE_SCREEN = "[data-screen]:visible"
PREVIEW_VIDEO = "#prejoinVideo"
PREVIEW_ICON = "#prejoinPlaceholder"
OWN_MUTE_MARK = "#localContainer .name .muted"
OTHER_MUTE_MARK = f"{OTHER_TILE} .name .muted"
MEDIA_REFUSED = (
    "No camera or microphone available (NotAllowedError). Please connect a device or grant permission and try again."
)
REFUSE_ALL_MEDIA = """
navigator.mediaDevices.getUserMedia = async () => {
    throw new DOMException("permission denied", "NotAllowedError");
};
"""
PREVIEW_PLAYING = "() => document.getElementById('prejoinVideo').videoWidth > 0"
SOCKETS_OPENED = "() => window.socketsOpened"
LIVE_TRACKS_OF_KIND = """
(kind) => window.requestedTracks.filter((track) => track.kind === kind && track.readyState === "live")
"""
LIVE_VIDEO_TRACK_COUNT = f"() => ({LIVE_TRACKS_OF_KIND})('video').length"
LIVE_AUDIO_TRACKS_ENABLED = f"() => ({LIVE_TRACKS_OF_KIND})('audio').map((track) => track.enabled)"
WHAT_THE_PREJOIN_SHOWS = """
() => ({
    screen: document.querySelector("[data-screen]:not(.hidden)").dataset.screen,
    microphoneOff: document.getElementById("prejoinMicBtn").getAttribute("aria-pressed"),
    cameraOff: document.getElementById("prejoinCamBtn").getAttribute("aria-pressed"),
    start: document.getElementById("prejoinStartBtn").textContent,
    mediaRequests: window.mediaRequests,
})
"""
DOM_CLICK = "(selector) => document.querySelector(selector).click()"
MARK_THE_DOCUMENT = "() => { window.markedDocument = true; }"
DOCUMENT_IS_MARKED = "() => window.markedDocument === true"
A_CONNECTION_IS_CONNECTED = """
() => window.recordedConnections.some((connection) => connection.connectionState === "connected")
"""
SENT_TRACKS = """
() => {
    const tracks = window.recordedConnections
        .filter((connection) => connection.connectionState === "connected")
        .flatMap((connection) => connection.getSenders())
        .map((sender) => sender.track)
        .filter((track) => track !== null);
    return {
        video: tracks.filter((track) => track.kind === "video").length,
        audioEnabled: tracks.filter((track) => track.kind === "audio").map((track) => track.enabled),
    };
}
"""


@dataclass
class Host:
    page: Page
    ice_server_requests: list[str] = field(default_factory=list)

    def record_ice_server_request(self, request: Request) -> None:
        if urlparse(request.url).path == ICE_SERVERS_PATH:
            self.ice_server_requests.append(request.url)


@dataclass(frozen=True)
class Choice:
    camera: bool
    microphone: bool
    label: str


@pytest.fixture
def open_host(open_person: Callable[..., Page], page_errors: list[str]) -> Callable[..., Host]:
    def open_recorded_host(form_factor: dict[str, Any] = DESKTOP, media_refusals: Sequence[str] = ()) -> Host:
        host = Host(record_page_errors(open_person(**form_factor), page_errors))
        for script in (*media_refusals, RECORD_MEDIA_TRACKS, COUNT_SOCKETS, RECORD_PEER_CONNECTIONS):
            host.page.add_init_script(script)
        host.page.on("request", host.record_ice_server_request)
        return host

    return open_recorded_host


def open_answered_prejoin(host: Host, base_url: str) -> None:
    open_prejoin(host.page, base_url)
    wait_until_the_prejoin_media_answered(host.page)


def controls(page: Page) -> list[Locator]:
    return [page.locator(selector) for selector in (PREJOIN_MICROPHONE, PREJOIN_CAMERA, PREJOIN_START)]


def expect_nothing_reached_the_server(host: Host) -> None:
    assert host.page.evaluate(SOCKETS_OPENED) == 0
    assert host.ice_server_requests == []


def press_until_off(page: Page, selector: str) -> None:
    button = page.locator(selector)
    button.click()
    expect(button).to_have_attribute("aria-pressed", "true")


def expect_the_prejoin_alone(page: Page) -> None:
    expect(page.locator(VISIBLE_SCREEN)).to_have_attribute("data-screen", "prejoin")


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_prejoin_plays_the_own_camera_mirrored_with_both_on_and_reaches_no_server(
    open_host: Callable[..., Host], base_url: str, form_factor: dict[str, Any]
) -> None:
    host = open_host(form_factor)

    open_answered_prejoin(host, base_url)

    expect_the_prejoin_alone(host.page)
    host.page.wait_for_function(PREVIEW_PLAYING, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    expect(host.page.locator(PREVIEW_VIDEO)).to_be_visible()
    expect(host.page.locator(PREVIEW_VIDEO)).to_have_css("transform", MIRRORED)
    for selector in (PREJOIN_MICROPHONE, PREJOIN_CAMERA):
        expect(host.page.locator(selector)).to_have_attribute("aria-pressed", "false")
    expect(host.page.locator(PREJOIN_START)).to_have_text(BOTH_ON)
    expect(host.page).to_have_url(f"{base_url}/")
    expect_nothing_reached_the_server(host)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_prejoin_camera_off_turns_the_light_off_and_on_brings_the_picture_back(
    open_host: Callable[..., Host], base_url: str, form_factor: dict[str, Any]
) -> None:
    host = open_host(form_factor)
    open_answered_prejoin(host, base_url)
    start = host.page.locator(PREJOIN_START)
    icon = host.page.locator(PREVIEW_ICON)

    press_until_off(host.page, PREJOIN_CAMERA)

    expect(icon.locator(".av svg")).to_be_visible()
    expect(icon).to_have_css("background-color", CAMERA_OFF_SURFACE)
    expect(start).to_have_text(MIC_ONLY)
    assert host.page.evaluate(LIVE_VIDEO_TRACK_COUNT) == 0

    host.page.click(PREJOIN_CAMERA)

    expect(start).to_have_text(BOTH_ON)
    expect(icon).to_be_hidden()
    expect(host.page.locator(PREVIEW_VIDEO)).to_be_visible()
    host.page.wait_for_function(PREVIEW_PLAYING, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    assert host.page.evaluate(LIVE_VIDEO_TRACK_COUNT) == 1
    expect_nothing_reached_the_server(host)


def expect_the_host_sees_the_choice(host: Page, choice: Choice) -> None:
    own_picture, own_icon = host.locator("#localVideo"), host.locator("#localPlaceholder")
    shown, hidden = (own_picture, own_icon) if choice.camera else (own_icon, own_picture)
    expect(shown).to_be_visible()
    expect(hidden).to_be_hidden()
    if choice.camera:
        host.wait_for_function(LOCAL_VIDEO_PLAYING, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    expect(host.locator(OWN_MUTE_MARK)).to_be_visible(visible=not choice.microphone)
    expect(host.locator("#videoBtn")).to_have_text("Cam On" if choice.camera else "Cam Off")
    expect(host.locator("#muteBtn")).to_have_text("Mic On" if choice.microphone else "Mic Off")


def expect_the_guest_sees_the_choice(guest: Page, choice: Choice) -> None:
    if choice.camera:
        wait_for_the_picture(guest)
    else:
        expect(guest.locator(ICON_ONLY_ON_THE_TILES)).to_be_visible()
    expect(guest.locator(OTHER_MUTE_MARK)).to_be_visible(visible=not choice.microphone)


@pytest.mark.parametrize(
    ("form_factor", "choice"),
    [
        pytest.param(DESKTOP, Choice(camera=True, microphone=True, label=BOTH_ON), id="desktop-both-on"),
        pytest.param(DESKTOP, Choice(camera=True, microphone=False, label=CAMERA_ONLY), id="desktop-camera-only"),
        pytest.param(DESKTOP, Choice(camera=False, microphone=True, label=MIC_ONLY), id="desktop-mic-only"),
        pytest.param(DESKTOP, Choice(camera=False, microphone=False, label=BOTH_OFF), id="desktop-both-off"),
        pytest.param(PHONE, Choice(camera=False, microphone=False, label=BOTH_OFF), id="phone-both-off"),
    ],
)
def test_prejoin_choice_is_what_the_call_starts_with_for_the_host_and_the_guest(
    open_host: Callable[..., Host],
    open_person: Callable[..., Page],
    base_url: str,
    page_errors: list[str],
    form_factor: dict[str, Any],
    choice: Choice,
) -> None:
    host = open_host(form_factor)
    open_answered_prejoin(host, base_url)
    if not choice.camera:
        press_until_off(host.page, PREJOIN_CAMERA)
    if not choice.microphone:
        press_until_off(host.page, PREJOIN_MICROPHONE)
    start = host.page.locator(PREJOIN_SCREEN).get_by_role("button", name=choice.label, exact=True)
    expect(start).to_be_enabled()
    expect_nothing_reached_the_server(host)

    start.click()
    expect(host.page.locator("#status")).to_have_text(IN_CALL)
    expect_the_host_sees_the_choice(host.page, choice)
    guest = join(open_person, host.page.input_value("#shareUrl"), form_factor, (RECORD_PEER_CONNECTIONS,), page_errors)
    for person in (host.page, guest):
        person.wait_for_function(A_CONNECTION_IS_CONNECTED, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

    expect_the_guest_sees_the_choice(guest, choice)
    sent = {"video": int(choice.camera), "audioEnabled": [choice.microphone]}
    assert host.page.evaluate(SENT_TRACKS) == sent


def expect_the_desktop_texts(prejoin: Locator) -> None:
    expect(prejoin.get_by_role("heading", name="Ready to start?")).to_be_visible()
    expect(prejoin.get_by_text("You'll get a link to invite people.")).to_be_visible()
    expect(prejoin.get_by_role("button", name="Back")).to_be_hidden()
    expect(prejoin.get_by_text("New call")).to_be_hidden()


def expect_the_phone_texts(prejoin: Locator) -> None:
    expect(prejoin.get_by_text("New call")).to_be_visible()
    expect(prejoin.get_by_role("button", name="Back")).to_be_visible()
    expect(prejoin.locator(".self-row .av svg")).to_be_visible()
    expect(prejoin.locator(".self-row")).to_have_text("You")
    expect(prejoin.get_by_role("heading", name="Ready to start?")).to_be_hidden()


@pytest.mark.parametrize(
    ("form_factor", "expect_the_texts"),
    [
        pytest.param(DESKTOP, expect_the_desktop_texts, id="desktop"),
        pytest.param(PHONE, expect_the_phone_texts, id="phone"),
    ],
)
def test_prejoin_texts_fit_the_form_factor(
    open_host: Callable[..., Host],
    base_url: str,
    form_factor: dict[str, Any],
    expect_the_texts: Callable[[Locator], None],
) -> None:
    host = open_host(form_factor)

    open_answered_prejoin(host, base_url)

    expect_the_texts(host.page.locator(PREJOIN_SCREEN))


@pytest.mark.parametrize(
    ("form_factor", "way_home"),
    [pytest.param(DESKTOP, "HopIn", id="desktop-brand"), pytest.param(PHONE, "Back", id="phone-back")],
)
def test_prejoin_brand_or_back_opens_the_start_page_as_a_new_document(
    open_host: Callable[..., Host], base_url: str, form_factor: dict[str, Any], way_home: str
) -> None:
    host = open_host(form_factor)
    open_answered_prejoin(host, base_url)
    host.page.evaluate(MARK_THE_DOCUMENT)

    host.page.locator(PREJOIN_SCREEN).get_by_role("button", name=way_home, exact=True).click()

    expect(host.page.locator("[data-screen=home]")).to_be_visible()
    expect(host.page).to_have_url(f"{base_url}/")
    assert not host.page.evaluate(DOCUMENT_IS_MARKED)


def hold_the_first_request(host: Host, base_url: str) -> None:
    host.page.add_init_script(HOLD_MEDIA)
    open_prejoin(host.page, base_url)


def hold_the_camera_coming_back(host: Host, base_url: str) -> None:
    open_answered_prejoin(host, base_url)
    press_until_off(host.page, PREJOIN_CAMERA)
    host.page.evaluate(HOLD_MEDIA)
    host.page.click(PREJOIN_CAMERA)


@pytest.mark.parametrize(
    ("hold_a_media_request", "pressed"),
    [
        pytest.param(hold_the_first_request, PREJOIN_MICROPHONE, id="first-request-microphone"),
        pytest.param(hold_the_camera_coming_back, PREJOIN_CAMERA, id="camera-request-camera"),
        pytest.param(hold_the_camera_coming_back, PREJOIN_START, id="camera-request-start"),
    ],
)
def test_prejoin_ignores_its_controls_while_a_media_request_is_pending(
    open_host: Callable[..., Host],
    base_url: str,
    hold_a_media_request: Callable[[Host, str], None],
    pressed: str,
) -> None:
    host = open_host()
    hold_a_media_request(host, base_url)
    host.page.wait_for_function(MEDIA_REQUEST_IS_HELD)
    for control in controls(host.page):
        expect(control).to_be_disabled()
    shown_before_the_press = host.page.evaluate(WHAT_THE_PREJOIN_SHOWS)

    host.page.evaluate(DOM_CLICK, pressed)

    assert host.page.evaluate(WHAT_THE_PREJOIN_SHOWS) == shown_before_the_press
    expect_the_prejoin_alone(host.page)
    expect_nothing_reached_the_server(host)

    host.page.evaluate(RELEASE_MEDIA)

    for control in controls(host.page):
        expect(control).to_be_enabled()
    for selector in (PREJOIN_MICROPHONE, PREJOIN_CAMERA):
        expect(host.page.locator(selector)).to_have_attribute("aria-pressed", "false")
    expect(host.page.locator(PREJOIN_START)).to_have_text(BOTH_ON)
    assert host.page.evaluate(LIVE_VIDEO_TRACK_COUNT) == 1
    assert host.page.evaluate(LIVE_AUDIO_TRACKS_ENABLED) == [True]


def test_prejoin_with_camera_and_microphone_refused_shows_why_and_offers_nothing_to_press(
    open_host: Callable[..., Host], base_url: str
) -> None:
    host = open_host(media_refusals=(REFUSE_ALL_MEDIA,))

    open_prejoin(host.page, base_url)

    expect(host.page.locator("#prejoinMessage")).to_have_text(MEDIA_REFUSED)
    for control in controls(host.page):
        expect(control).to_be_disabled()
    expect_the_prejoin_alone(host.page)
    expect_nothing_reached_the_server(host)


def test_prejoin_without_a_camera_starts_with_the_mic_and_asks_again_when_the_camera_is_pressed(
    open_host: Callable[..., Host], base_url: str
) -> None:
    host = open_host(media_refusals=(REFUSE_THE_CAMERA,))
    open_answered_prejoin(host, base_url)
    camera = host.page.locator(PREJOIN_CAMERA)
    start = host.page.locator(PREJOIN_START)
    expect(host.page.locator(PREVIEW_ICON).locator(".av svg")).to_be_visible()
    expect(camera).to_have_attribute("aria-pressed", "true")
    expect(start).to_have_text(MIC_ONLY)
    requests_before_the_press = host.page.evaluate(MEDIA_REQUEST_COUNT)

    camera.click()

    for control in controls(host.page):
        expect(control).to_be_enabled()
    expect(camera).to_have_attribute("aria-pressed", "true")
    expect(start).to_have_text(MIC_ONLY)
    expect(host.page.locator(PREVIEW_ICON).locator(".av svg")).to_be_visible()
    assert host.page.evaluate(MEDIA_REQUEST_COUNT) == requests_before_the_press + 1


def test_prejoin_buttons_name_their_action_and_tell_whether_the_device_is_off(
    open_host: Callable[..., Host], base_url: str
) -> None:
    host = open_host()
    open_answered_prejoin(host, base_url)
    prejoin = host.page.locator(PREJOIN_SCREEN)

    def button(name: str, *, off: bool) -> Locator:
        return prejoin.get_by_role("button", name=name, exact=True, pressed=off)

    expect(button("Mute", off=False)).to_be_visible()
    expect(button("Camera", off=False)).to_be_visible()

    button("Mute", off=False).click()
    button("Camera", off=False).click()
    expect(button("Unmute", off=True)).to_be_visible()
    expect(button("Camera", off=True)).to_be_visible()

    button("Unmute", off=True).click()
    button("Camera", off=True).click()
    expect(button("Mute", off=False)).to_be_visible()
    expect(button("Camera", off=False)).to_be_visible()
