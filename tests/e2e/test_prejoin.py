from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Locator, Page, Request, Route, expect

from hopin.config.constants import ICE_SERVERS_PATH
from tests.e2e.conftest import (
    COUNT_SOCKETS,
    DESKTOP,
    FORM_FACTORS,
    HOLD_MEDIA,
    ICE_SERVERS_URL_PATTERN,
    ICON_ONLY_ON_THE_TILES,
    INVITE_LINK_PATTERN,
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
    add_init_scripts,
    expect_both_legal_links,
    join,
    open_prejoin,
    open_prejoin_by_link,
    press_through_the_prejoin,
    record_page_errors,
    start_call,
    wait_for_the_picture,
    wait_until_the_prejoin_media_answered,
)

pytestmark = pytest.mark.e2e

IN_CALL = "In call"
BOTH_ON = "Start with camera and mic"
CAMERA_ONLY = "Start with camera only"
MIC_ONLY = "Start with mic only"
BOTH_OFF = "Start with camera and mic off"
JOIN_BOTH_ON = "Join with camera and mic"
JOIN_CAMERA_ONLY = "Join with camera only"
JOIN_MIC_ONLY = "Join with mic only"
JOIN_BOTH_OFF = "Join with camera and mic off"
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
HISTORY_LENGTH = "() => history.length"
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
PARTICIPANT_COUNT = "#participantCount .num"
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


class Kind(Enum):
    HOST = "host"
    GUEST = "guest"


@dataclass(frozen=True)
class KindTexts:
    heading: str
    phone_title: str
    sub_line: str | None
    both_on: str


TEXTS = {
    Kind.HOST: KindTexts("Ready to start?", "New call", "You'll get a link to invite people.", BOTH_ON),
    Kind.GUEST: KindTexts("Ready to join?", "Join call", None, JOIN_BOTH_ON),
}
KINDS = [pytest.param(kind, id=kind.value) for kind in Kind]


@dataclass
class Person:
    page: Page
    ice_server_requests: list[str] = field(default_factory=list)

    def record_ice_server_request(self, request: Request) -> None:
        if urlparse(request.url).path == ICE_SERVERS_PATH:
            self.ice_server_requests.append(request.url)


@dataclass(frozen=True)
class Choice:
    camera: bool
    microphone: bool
    start_label: str
    join_label: str

    def label_for(self, kind: Kind) -> str:
        return self.start_label if kind is Kind.HOST else self.join_label


@pytest.fixture
def open_recorded(open_person: Callable[..., Page], page_errors: list[str]) -> Callable[..., Person]:
    def open_recorded_person(
        form_factor: dict[str, Any] = DESKTOP,
        media_refusals: Sequence[str] = (),
        scripts_after_the_recorder: Sequence[str] = (),
    ) -> Person:
        person = Person(record_page_errors(open_person(**form_factor), page_errors))
        add_init_scripts(
            person.page,
            (
                *media_refusals,
                RECORD_MEDIA_TRACKS,
                COUNT_SOCKETS,
                RECORD_PEER_CONNECTIONS,
                *scripts_after_the_recorder,
            ),
        )
        person.page.on("request", person.record_ice_server_request)
        return person

    return open_recorded_person


def open_answered_prejoin(person: Person, base_url: str) -> None:
    open_prejoin(person.page, base_url)
    wait_until_the_prejoin_media_answered(person.page)


@pytest.fixture
def start_a_call_to_join(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> Callable[..., Page]:
    def start(scripts: Sequence[str] = ()) -> Page:
        calling_host = record_page_errors(open_person(**DESKTOP), page_errors)
        add_init_scripts(calling_host, scripts)
        start_call(calling_host, base_url)
        return calling_host

    return start


@pytest.fixture
def open_answered_prejoin_of(base_url: str, start_a_call_to_join: Callable[..., Page]) -> Callable[..., Page | None]:
    def open_(kind: Kind, person: Person, calling_host_scripts: Sequence[str] = ()) -> Page | None:
        calling_host = None
        if kind is Kind.HOST:
            open_prejoin(person.page, base_url)
        else:
            calling_host = start_a_call_to_join(calling_host_scripts)
            open_prejoin_by_link(person.page, calling_host.input_value("#shareUrl"))
        wait_until_the_prejoin_media_answered(person.page)
        return calling_host

    return open_


def controls(page: Page) -> list[Locator]:
    return [page.locator(selector) for selector in (PREJOIN_MICROPHONE, PREJOIN_CAMERA, PREJOIN_START)]


def expect_nothing_reached_the_server(person: Person) -> None:
    assert person.page.evaluate(SOCKETS_OPENED) == 0
    assert person.ice_server_requests == []


def press_until_off(page: Page, selector: str) -> None:
    button = page.locator(selector)
    button.click()
    expect(button).to_have_attribute("aria-pressed", "true")


def expect_the_prejoin_alone(page: Page) -> None:
    expect(page.locator(VISIBLE_SCREEN)).to_have_attribute("data-screen", "prejoin")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_prejoin_plays_the_own_camera_mirrored_with_both_on_and_reaches_no_server(
    open_recorded: Callable[..., Person],
    open_answered_prejoin_of: Callable[..., Page | None],
    base_url: str,
    form_factor: dict[str, Any],
    kind: Kind,
) -> None:
    person = open_recorded(form_factor)

    calling_host = open_answered_prejoin_of(kind, person)

    expect_the_prejoin_alone(person.page)
    person.page.wait_for_function(PREVIEW_PLAYING, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    expect(person.page.locator(PREVIEW_VIDEO)).to_be_visible()
    expect(person.page.locator(PREVIEW_VIDEO)).to_have_css("transform", MIRRORED)
    for selector in (PREJOIN_MICROPHONE, PREJOIN_CAMERA):
        expect(person.page.locator(selector)).to_have_attribute("aria-pressed", "false")
    expect(person.page.locator(PREJOIN_START)).to_have_text(TEXTS[kind].both_on)
    expect_nothing_reached_the_server(person)
    if calling_host is None:
        expect(person.page).to_have_url(f"{base_url}/")
    else:
        expect(person.page).to_have_url(calling_host.input_value("#shareUrl"))
        expect(calling_host.locator(OTHER_TILE)).to_have_count(0)
        expect(calling_host.locator(PARTICIPANT_COUNT)).to_have_text("1")


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_started_call_puts_its_link_in_the_address_without_a_new_history_entry(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host = record_page_errors(open_person(**form_factor), page_errors)
    open_prejoin(host, base_url)
    history_length = host.evaluate(HISTORY_LENGTH)

    press_through_the_prejoin(host)
    expect(host.locator("#shareUrl")).to_have_value(INVITE_LINK_PATTERN)

    expect(host).to_have_url(host.input_value("#shareUrl"))
    assert host.evaluate(HISTORY_LENGTH) == history_length


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_guest_who_opens_the_hosts_address_joins_the_call(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host = record_page_errors(open_person(**form_factor), page_errors)
    start_call(host, base_url)

    guest = join(open_person, host.url, form_factor, page_errors=page_errors)

    for person in (host, guest):
        expect(person.locator(OTHER_TILE)).to_have_count(1, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_prejoin_camera_off_turns_the_light_off_and_on_brings_the_picture_back(
    open_recorded: Callable[..., Person], base_url: str, form_factor: dict[str, Any]
) -> None:
    person = open_recorded(form_factor)
    open_answered_prejoin(person, base_url)
    start = person.page.locator(PREJOIN_START)
    icon = person.page.locator(PREVIEW_ICON)

    press_until_off(person.page, PREJOIN_CAMERA)

    expect(icon.locator(".av svg")).to_be_visible()
    expect(icon).to_have_css("background-color", CAMERA_OFF_SURFACE)
    expect(start).to_have_text(MIC_ONLY)
    assert person.page.evaluate(LIVE_VIDEO_TRACK_COUNT) == 0

    person.page.click(PREJOIN_CAMERA)

    expect(start).to_have_text(BOTH_ON)
    expect(icon).to_be_hidden()
    expect(person.page.locator(PREVIEW_VIDEO)).to_be_visible()
    person.page.wait_for_function(PREVIEW_PLAYING, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    assert person.page.evaluate(LIVE_VIDEO_TRACK_COUNT) == 1
    expect_nothing_reached_the_server(person)


def expect_the_own_view_shows_the_choice(
    person: Page, choice: Choice, form_factor: dict[str, Any], *, alone: bool
) -> None:
    own_picture, own_icon = person.locator("#localVideo"), person.locator("#localPlaceholder")
    shown, hidden = (own_picture, own_icon) if choice.camera else (own_icon, own_picture)
    expect(shown).to_be_visible()
    expect(hidden).to_be_hidden()
    if choice.camera:
        person.wait_for_function(LOCAL_VIDEO_PLAYING, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    if alone or form_factor == DESKTOP:
        # Only a phone hides the own mark once others are in the call; it is the others' to see there.
        expect(person.locator(OWN_MUTE_MARK)).to_be_visible(visible=not choice.microphone)
    expect(person.locator("#videoBtn")).to_have_text("Cam On" if choice.camera else "Cam Off")
    expect(person.locator("#muteBtn")).to_have_text("Mic On" if choice.microphone else "Mic Off")


def expect_the_other_sees_the_choice(other: Page, choice: Choice) -> None:
    if choice.camera:
        wait_for_the_picture(other)
    else:
        expect(other.locator(ICON_ONLY_ON_THE_TILES)).to_be_visible()
    expect(other.locator(OTHER_MUTE_MARK)).to_be_visible(visible=not choice.microphone)


CHOICES = [
    pytest.param(DESKTOP, Choice(True, True, BOTH_ON, JOIN_BOTH_ON), id="desktop-both-on"),
    pytest.param(DESKTOP, Choice(True, False, CAMERA_ONLY, JOIN_CAMERA_ONLY), id="desktop-camera-only"),
    pytest.param(DESKTOP, Choice(False, True, MIC_ONLY, JOIN_MIC_ONLY), id="desktop-mic-only"),
    pytest.param(DESKTOP, Choice(False, False, BOTH_OFF, JOIN_BOTH_OFF), id="desktop-both-off"),
    pytest.param(PHONE, Choice(False, False, BOTH_OFF, JOIN_BOTH_OFF), id="phone-both-off"),
]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(("form_factor", "choice"), CHOICES)
def test_prejoin_choice_is_what_the_call_starts_with(
    open_recorded: Callable[..., Person],
    open_person: Callable[..., Page],
    open_answered_prejoin_of: Callable[..., Page | None],
    page_errors: list[str],
    kind: Kind,
    form_factor: dict[str, Any],
    choice: Choice,
) -> None:
    chooser = open_recorded(form_factor)
    calling_host = open_answered_prejoin_of(kind, chooser, (RECORD_PEER_CONNECTIONS,))
    if not choice.camera:
        press_until_off(chooser.page, PREJOIN_CAMERA)
    if not choice.microphone:
        press_until_off(chooser.page, PREJOIN_MICROPHONE)
    start = chooser.page.locator(PREJOIN_SCREEN).get_by_role("button", name=choice.label_for(kind), exact=True)
    expect(start).to_be_enabled()
    expect_nothing_reached_the_server(chooser)
    if not choice.camera:
        assert chooser.page.evaluate(LIVE_VIDEO_TRACK_COUNT) == 0

    start.click()
    expect(chooser.page.locator("#status")).to_have_text(IN_CALL)
    expect_the_own_view_shows_the_choice(chooser.page, choice, form_factor, alone=calling_host is None)
    other = calling_host or join(
        open_person, chooser.page.input_value("#shareUrl"), form_factor, (RECORD_PEER_CONNECTIONS,), page_errors
    )
    for person in (chooser.page, other):
        person.wait_for_function(A_CONNECTION_IS_CONNECTED, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

    expect_the_other_sees_the_choice(other, choice)
    sent = {"video": int(choice.camera), "audioEnabled": [choice.microphone]}
    assert chooser.page.evaluate(SENT_TRACKS) == sent


@pytest.mark.parametrize("kind", KINDS)
def test_prejoin_off_choice_shows_on_the_call_buttons_while_the_ice_servers_load(
    open_recorded: Callable[..., Person], open_answered_prejoin_of: Callable[..., Page | None], kind: Kind
) -> None:
    person = open_recorded()
    open_answered_prejoin_of(kind, person)
    for selector in (PREJOIN_CAMERA, PREJOIN_MICROPHONE):
        press_until_off(person.page, selector)
    held_ice_requests: list[Route] = []
    person.page.route(ICE_SERVERS_URL_PATTERN, lambda route: held_ice_requests.append(route))
    with person.page.expect_request(ICE_SERVERS_URL_PATTERN):
        person.page.click(PREJOIN_START)
    microphone = person.page.locator("#muteBtn")

    expect(microphone).to_have_text("Mic Off")
    assert person.page.evaluate(LIVE_AUDIO_TRACKS_ENABLED) == [False]
    expect(person.page.locator("#videoBtn")).to_have_text("Cam Off")
    assert person.page.evaluate(LIVE_VIDEO_TRACK_COUNT) == 0

    microphone.click()

    expect(microphone).to_have_text("Mic On")
    assert person.page.evaluate(LIVE_AUDIO_TRACKS_ENABLED) == [True]
    held_ice_requests.pop().continue_()


def expect_the_desktop_texts(prejoin: Locator, texts: KindTexts) -> None:
    expect(prejoin.get_by_role("heading", name=texts.heading)).to_be_visible()
    if texts.sub_line is None:
        expect(prejoin.locator(".prejoin-sub")).to_be_hidden()
    else:
        expect(prejoin.get_by_text(texts.sub_line)).to_be_visible()
    expect(prejoin.get_by_role("button", name="Back")).to_be_hidden()
    expect(prejoin.get_by_text(texts.phone_title)).to_be_hidden()
    expect_both_legal_links(prejoin)


def expect_the_phone_texts(prejoin: Locator, texts: KindTexts) -> None:
    expect(prejoin.get_by_text(texts.phone_title)).to_be_visible()
    expect(prejoin.get_by_role("button", name="Back")).to_be_visible()
    expect(prejoin.locator(".self-row .av svg")).to_be_visible()
    expect(prejoin.locator(".self-row")).to_have_text("You")
    expect(prejoin.get_by_role("heading", name=texts.heading)).to_be_hidden()
    expect_both_legal_links(prejoin)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    ("form_factor", "expect_the_texts"),
    [
        pytest.param(DESKTOP, expect_the_desktop_texts, id="desktop"),
        pytest.param(PHONE, expect_the_phone_texts, id="phone"),
    ],
)
def test_prejoin_texts_fit_the_kind_and_the_form_factor(
    open_recorded: Callable[..., Person],
    open_answered_prejoin_of: Callable[..., Page | None],
    kind: Kind,
    form_factor: dict[str, Any],
    expect_the_texts: Callable[[Locator, KindTexts], None],
) -> None:
    person = open_recorded(form_factor)

    open_answered_prejoin_of(kind, person)

    expect_the_texts(person.page.locator(PREJOIN_SCREEN), TEXTS[kind])


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    ("form_factor", "way_home"),
    [pytest.param(DESKTOP, "HopIn", id="desktop-brand"), pytest.param(PHONE, "Back", id="phone-back")],
)
def test_prejoin_brand_or_back_opens_the_start_page_as_a_new_document(
    open_recorded: Callable[..., Person],
    open_answered_prejoin_of: Callable[..., Page | None],
    base_url: str,
    kind: Kind,
    form_factor: dict[str, Any],
    way_home: str,
) -> None:
    person = open_recorded(form_factor)
    open_answered_prejoin_of(kind, person)
    person.page.evaluate(MARK_THE_DOCUMENT)
    expect_nothing_reached_the_server(person)

    person.page.locator(PREJOIN_SCREEN).get_by_role("button", name=way_home, exact=True).click()

    expect(person.page.locator("[data-screen=home]")).to_be_visible()
    expect(person.page).to_have_url(f"{base_url}/")
    assert not person.page.evaluate(DOCUMENT_IS_MARKED)


def hold_the_first_request(open_recorded: Callable[..., Person], base_url: str) -> Person:
    person = open_recorded(scripts_after_the_recorder=(HOLD_MEDIA,))
    open_prejoin(person.page, base_url)
    return person


def hold_the_camera_coming_back(open_recorded: Callable[..., Person], base_url: str) -> Person:
    person = open_recorded()
    open_answered_prejoin(person, base_url)
    press_until_off(person.page, PREJOIN_CAMERA)
    person.page.evaluate(HOLD_MEDIA)
    person.page.click(PREJOIN_CAMERA)
    return person


def expect_the_controls_to_wait_for_the_release(person: Person, pressed: str, both_on: str) -> None:
    person.page.wait_for_function(MEDIA_REQUEST_IS_HELD)
    for control in controls(person.page):
        expect(control).to_be_disabled()
    shown_before_the_press = person.page.evaluate(WHAT_THE_PREJOIN_SHOWS)

    person.page.evaluate(DOM_CLICK, pressed)

    assert person.page.evaluate(WHAT_THE_PREJOIN_SHOWS) == shown_before_the_press
    expect_the_prejoin_alone(person.page)
    expect_nothing_reached_the_server(person)

    person.page.evaluate(RELEASE_MEDIA)

    for control in controls(person.page):
        expect(control).to_be_enabled()
    for selector in (PREJOIN_MICROPHONE, PREJOIN_CAMERA):
        expect(person.page.locator(selector)).to_have_attribute("aria-pressed", "false")
    expect(person.page.locator(PREJOIN_START)).to_have_text(both_on)
    assert person.page.evaluate(LIVE_VIDEO_TRACK_COUNT) == 1
    assert person.page.evaluate(LIVE_AUDIO_TRACKS_ENABLED) == [True]


@pytest.mark.parametrize(
    ("hold_a_media_request", "pressed"),
    [
        pytest.param(hold_the_first_request, PREJOIN_MICROPHONE, id="first-request-microphone"),
        pytest.param(hold_the_camera_coming_back, PREJOIN_CAMERA, id="camera-request-camera"),
        pytest.param(hold_the_camera_coming_back, PREJOIN_START, id="camera-request-start"),
    ],
)
def test_prejoin_ignores_its_controls_while_a_media_request_is_pending(
    open_recorded: Callable[..., Person],
    base_url: str,
    hold_a_media_request: Callable[[Callable[..., Person], str], Person],
    pressed: str,
) -> None:
    person = hold_a_media_request(open_recorded, base_url)
    expect_the_controls_to_wait_for_the_release(person, pressed, BOTH_ON)


def test_guest_prejoin_ignores_join_while_its_first_media_request_is_pending(
    open_recorded: Callable[..., Person], start_a_call_to_join: Callable[..., Page]
) -> None:
    calling_host = start_a_call_to_join()
    guest = open_recorded(scripts_after_the_recorder=(HOLD_MEDIA,))
    open_prejoin_by_link(guest.page, calling_host.input_value("#shareUrl"))

    expect_the_controls_to_wait_for_the_release(guest, PREJOIN_START, JOIN_BOTH_ON)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_guest_who_left_with_the_camera_off_gets_a_fresh_prejoin_with_both_on_at_rejoin(
    open_recorded: Callable[..., Person],
    open_answered_prejoin_of: Callable[..., Page | None],
    form_factor: dict[str, Any],
) -> None:
    guest = open_recorded(form_factor)
    open_answered_prejoin_of(Kind.GUEST, guest)
    press_through_the_prejoin(guest.page, with_camera=False)
    expect(guest.page.locator("#status")).to_have_text(IN_CALL)
    guest.page.click("#leaveBtn")
    guest.ice_server_requests.clear()

    guest.page.get_by_role("button", name="Rejoin").click()
    wait_until_the_prejoin_media_answered(guest.page)

    expect_the_prejoin_alone(guest.page)
    guest.page.wait_for_function(PREVIEW_PLAYING, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    for selector in (PREJOIN_MICROPHONE, PREJOIN_CAMERA):
        expect(guest.page.locator(selector)).to_have_attribute("aria-pressed", "false")
    expect(guest.page.locator(PREJOIN_START)).to_have_text(JOIN_BOTH_ON)
    expect_nothing_reached_the_server(guest)
    press_through_the_prejoin(guest.page)
    expect(guest.page.locator("#status")).to_have_text(IN_CALL)


def test_prejoin_with_camera_and_microphone_refused_shows_why_and_offers_nothing_to_press(
    open_recorded: Callable[..., Person], base_url: str
) -> None:
    person = open_recorded(media_refusals=(REFUSE_ALL_MEDIA,))

    open_prejoin(person.page, base_url)

    expect(person.page.locator("#prejoinMessage")).to_have_text(MEDIA_REFUSED)
    for control in controls(person.page):
        expect(control).to_be_disabled()
    expect_the_prejoin_alone(person.page)
    expect_nothing_reached_the_server(person)


def test_prejoin_without_a_camera_starts_with_the_mic_and_asks_again_when_the_camera_is_pressed(
    open_recorded: Callable[..., Person], base_url: str
) -> None:
    person = open_recorded(media_refusals=(REFUSE_THE_CAMERA,))
    open_answered_prejoin(person, base_url)
    camera = person.page.locator(PREJOIN_CAMERA)
    start = person.page.locator(PREJOIN_START)
    expect(person.page.locator(PREVIEW_ICON).locator(".av svg")).to_be_visible()
    expect(camera).to_have_attribute("aria-pressed", "true")
    expect(start).to_have_text(MIC_ONLY)
    requests_before_the_press = person.page.evaluate(MEDIA_REQUEST_COUNT)

    camera.click()

    for control in controls(person.page):
        expect(control).to_be_enabled()
    expect(camera).to_have_attribute("aria-pressed", "true")
    expect(start).to_have_text(MIC_ONLY)
    expect(person.page.locator(PREVIEW_ICON).locator(".av svg")).to_be_visible()
    assert person.page.evaluate(MEDIA_REQUEST_COUNT) == requests_before_the_press + 1


def test_prejoin_buttons_name_their_action_and_tell_whether_the_device_is_off(
    open_recorded: Callable[..., Person], base_url: str
) -> None:
    person = open_recorded()
    open_answered_prejoin(person, base_url)
    prejoin = person.page.locator(PREJOIN_SCREEN)

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
