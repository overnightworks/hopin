import re
import socket
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any

import pytest
import uvicorn
from playwright.sync_api import Browser, BrowserContext, Locator, Page, expect
from webauth.config import WebAuthConfig, installed_web_auth_config

from hopin.app import create_app
from hopin.auth.stores import SignInStores
from hopin.config.constants import ICE_SERVERS_PATH
from hopin.main import uvicorn_options
from tests.sign_in_settings import build_sign_in_settings
from tests.signed_in import SeededMember, seed_member

LOOPBACK_HOST = "127.0.0.1"
READINESS_POLL_INTERVAL_SECONDS = 0.1
READINESS_DEADLINE_SECONDS = 20
SERVER_STOP_TIMEOUT_SECONDS = 10
FAKE_MEDIA_BROWSER_ARGS = [
    "--use-fake-device-for-media-stream",
    "--use-fake-ui-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
]
MEDIA_PERMISSIONS = ["camera", "microphone"]
MEDIA_FLOWING_TIMEOUT_MILLISECONDS = 30_000
INVITE_LINK_PATTERN = re.compile(r"/c/\w+")
ICE_SERVERS_URL_PATTERN = f"**{ICE_SERVERS_PATH}"
DESKTOP = {"viewport": {"width": 1280, "height": 800}}
PHONE = {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True}
LEGAL_LINKS = {
    "Impressum": "https://hallucinai.de/impressum",
    "Datenschutz": "https://hallucinai.de/datenschutz#hopin",
}
FORM_FACTORS = [pytest.param(DESKTOP, id="desktop"), pytest.param(PHONE, id="phone")]
OTHER_TILE = "#videoGrid .tile:not(.self)"
OTHER_VIDEO = f"{OTHER_TILE} video"
OTHER_CAMERA_OFF = f"{OTHER_TILE} .vid.off"
PICTURE_ONLY_ON_THE_TILES = f"{OTHER_VIDEO}:not(.hidden)"
ICON_ONLY_ON_THE_TILES = f"{OTHER_CAMERA_OFF}:not(.hidden)"
SMALL_PHONE_LANDSCAPE = {"viewport": {"width": 568, "height": 320}, "is_mobile": True, "has_touch": True}
RECORD_PEER_CONNECTIONS = """
window.recordedConfigurations = [];
window.recordedConnections = [];
const NativePeerConnection = window.RTCPeerConnection;
window.RTCPeerConnection = function (configuration, ...rest) {
    window.recordedConfigurations.push(configuration);
    const connection = new NativePeerConnection(configuration, ...rest);
    window.recordedConnections.push(connection);
    return connection;
};
window.RTCPeerConnection.prototype = NativePeerConnection.prototype;
"""
OPEN_PEER_COUNT_REACHED = """
(count) => window.recordedConnections.filter((connection) => connection.signalingState !== "closed").length === count
"""
SHARE_THE_SCREEN_FROM_A_CANVAS = """
navigator.mediaDevices.getDisplayMedia = async () => document.createElement("canvas").captureStream();
"""
REFUSE_THE_CAMERA = """
const nativeGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
navigator.mediaDevices.getUserMedia = (constraints) => {
    if (constraints.video && !window.cameraPluggedIn) {
        return Promise.reject(new DOMException("no camera", "NotFoundError"));
    }
    return nativeGetUserMedia(constraints);
};
"""
PLUG_IN_THE_CAMERA = "() => { window.cameraPluggedIn = true; }"
COUNT_OFFERS = """
(() => {
    window.offerCount = 0;
    const nativeCreateOffer = RTCPeerConnection.prototype.createOffer;
    RTCPeerConnection.prototype.createOffer = function (...parameters) {
        window.offerCount += 1;
        return nativeCreateOffer.apply(this, parameters);
    };
})();
"""
OFFER_COUNT = "() => window.offerCount"
EVERY_VIDEO_CAP_IS = """
([peerCount, expectedCap]) => {
    const open = window.recordedConnections.filter((connection) => connection.signalingState !== "closed");
    const caps = open.flatMap((connection) => connection.getTransceivers()
        .filter((transceiver) => transceiver.receiver.track.kind === "video")
        .map((transceiver) => transceiver.sender.getParameters().encodings[0].maxBitrate));
    return open.length === peerCount && caps.length === peerCount && caps.every((cap) => cap === expectedCap);
}
"""
ONE_CAP_PER_CONNECTION = [1, 2_000_000]
TWO_CAPS_PER_CONNECTION = [2, 1_000_000]
THREE_CAPS_PER_CONNECTION = [3, 666_666]
FOUR_CAPS_PER_CONNECTION = [4, 500_000]
FIVE_CAPS_PER_CONNECTION = [5, 400_000]
AFTER_PENDING_REJECTIONS_ARE_REPORTED = """
() => new Promise((done) => {
    const channel = new MessageChannel();
    channel.port1.onmessage = () => done();
    channel.port2.postMessage(0);
})
"""
VIDEO_TRANSCEIVERS_PER_CONNECTION = """
() => window.recordedConnections
    .filter((connection) => connection.signalingState !== "closed")
    .map((connection) => connection.getTransceivers()
        .filter((transceiver) => transceiver.receiver.track.kind === "video").length)
"""
INBOUND_PER_CONNECTION = """
async ([kind, field]) => {
    const open = window.recordedConnections.filter((connection) => connection.signalingState !== "closed");
    return Promise.all(open.map(async (connection) => {
        const reports = [...(await connection.getStats()).values()];
        return reports
            .filter((report) => report.type === "inbound-rtp" && report.kind === kind)
            .reduce((total, report) => total + report[field], 0);
    }));
}
"""
INBOUND_HAS_GROWN = f"""
async ([kind, field, baseline]) => {{
    const now = await ({INBOUND_PER_CONNECTION})([kind, field]);
    return now.length === baseline.length && now.every((value, index) => value > baseline[index]);
}}
"""
TILE_AUDIO_IS_ALIVE_AND_UNMUTED = """
(selector) => {
    const video = document.querySelector(selector);
    const [audio] = video.srcObject?.getAudioTracks() ?? [];
    return audio?.readyState === "live" && audio.enabled && !video.muted && video.volume > 0;
}
"""
TILE_CLOCK_PASSED = "([selector, time]) => document.querySelector(selector).currentTime > time"
TILE_CURRENT_TIME = "(selector) => document.querySelector(selector).currentTime"
TILE_AUDIO_LEVEL = """
async ([selector, floor, timeoutMilliseconds, sampleIntervalMilliseconds]) => {
    const context = new AudioContext();
    try {
        await context.resume();
        const analyser = context.createAnalyser();
        context.createMediaStreamSource(document.querySelector(selector).srcObject).connect(analyser);
        const samples = new Uint8Array(analyser.fftSize);
        let loudest = 0;
        const end = performance.now() + timeoutMilliseconds;
        while (true) {
            analyser.getByteTimeDomainData(samples);
            loudest = samples.reduce((peak, sample) => Math.max(peak, Math.abs(sample - 128)), loudest);
            const remaining = end - performance.now();
            if (loudest > floor || remaining <= 0) break;
            await new Promise((next) => setTimeout(next, Math.floor(Math.min(sampleIntervalMilliseconds, remaining))));
        }
        return loudest;
    } finally {
        await context.close();
    }
}
"""
AUDIBLE_LEVEL_FLOOR = 4
AUDIO_SAMPLE_INTERVAL_MILLISECONDS = 50
LOCAL_VIDEO_PLAYING = "() => document.getElementById('localVideo').videoWidth > 0"
# The headless browser has no screen picker, so a canvas and a silent audio destination
# stand in for a shared screen with its sound.
RECORD_MEDIA_TRACKS = """
window.requestedTracks = [];
window.mediaRequests = 0;
window.heldMediaRequests = 0;
let mediaHeld = null;
window.holdMedia = () => {
    mediaHeld = new Promise((release) => {
        window.releaseMedia = () => {
            mediaHeld = null;
            release();
        };
    });
};
const recorded = (request) => async (constraints) => {
    window.mediaRequests += 1;
    if (mediaHeld) {
        window.heldMediaRequests += 1;
        await mediaHeld;
    }
    const stream = await request(constraints);
    window.requestedTracks.push(...stream.getTracks());
    return stream;
};
navigator.mediaDevices.getUserMedia = recorded(navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices));
navigator.mediaDevices.getDisplayMedia = recorded(async () => {
    const screenPicture = document.createElement("canvas").captureStream();
    const screenSound = new AudioContext().createMediaStreamDestination().stream;
    return new MediaStream([...screenPicture.getTracks(), ...screenSound.getTracks()]);
});
"""
COUNT_SOCKETS = """
window.socketsOpened = 0;
const NativeWebSocket = window.WebSocket;
window.WebSocket = class extends NativeWebSocket {
    constructor(...options) {
        super(...options);
        window.socketsOpened += 1;
    }
};
"""
HOLD_MEDIA = "window.holdMedia()"
RELEASE_MEDIA = "window.releaseMedia()"
MEDIA_REQUEST_COUNT = "() => window.mediaRequests"
MEDIA_REQUEST_IS_HELD = "() => window.heldMediaRequests > 0"
TRACKS_WERE_REQUESTED = "() => window.requestedTracks.length > 0"
MORE_TRACKS_THAN = "(count) => window.requestedTracks.length > count"
REQUESTED_TRACK_COUNT = "() => window.requestedTracks.length"
LIVE_TRACK_COUNT = "() => window.requestedTracks.filter((track) => track.readyState === 'live').length"
PREJOIN_MICROPHONE = "#prejoinMicBtn"
PREJOIN_CAMERA = "#prejoinCamBtn"
PREJOIN_START = "#prejoinStartBtn"


def _reserve_free_port() -> int:
    with socket.socket() as probe:
        probe.bind((LOOPBACK_HOST, 0))
        return int(probe.getsockname()[1])


@contextmanager
def _serving(server: uvicorn.Server) -> Iterator[None]:
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + READINESS_DEADLINE_SECONDS
    try:
        while not server.started:
            if not thread.is_alive():
                pytest.fail("HopIn exited before it started")
            if time.monotonic() > deadline:
                pytest.fail(f"HopIn did not start within {READINESS_DEADLINE_SECONDS} seconds")
            time.sleep(READINESS_POLL_INTERVAL_SECONDS)
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=SERVER_STOP_TIMEOUT_SECONDS)
        assert not thread.is_alive(), f"HopIn did not stop within {SERVER_STOP_TIMEOUT_SECONDS} seconds"


@dataclass(frozen=True)
class RunningServer:
    base_url: str
    config: WebAuthConfig
    stores: SignInStores


@pytest.fixture(scope="session")
def e2e_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[RunningServer]:
    port = _reserve_free_port()
    sign_in = build_sign_in_settings(
        tmp_path_factory.mktemp("sign-in"),
        host=LOOPBACK_HOST,
        port=port,
        public_origin=f"https://{LOOPBACK_HOST}:{port}",
    )
    stores = SignInStores.in_memory()
    app = create_app(sign_in.settings, stores=stores)
    server = uvicorn.Server(uvicorn.Config(app, **uvicorn_options(sign_in.settings)))
    with _serving(server):
        yield RunningServer(f"http://{LOOPBACK_HOST}:{port}", installed_web_auth_config(app), stores)


@pytest.fixture(scope="session")
def base_url(e2e_server: RunningServer) -> str:
    return e2e_server.base_url


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict[str, Any]) -> dict[str, Any]:
    return {**browser_type_launch_args, "args": FAKE_MEDIA_BROWSER_ARGS}


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict[str, Any]) -> dict[str, Any]:
    return {**browser_context_args, "permissions": MEDIA_PERMISSIONS}


def _playwright_cookie(set_cookie_header: str, config: WebAuthConfig) -> dict[str, Any]:
    cookie = SimpleCookie(set_cookie_header)
    ((name, morsel),) = cookie.items()
    expires = int(config.wall_clock()) + int(morsel["max-age"])
    # The URL form of add_cookies is refused for a __Host- name, so the cookie goes in by domain and path.
    return {
        "name": name,
        "value": morsel.value,
        "domain": LOOPBACK_HOST,
        "path": morsel["path"],
        "secure": bool(morsel["secure"]),
        "httpOnly": bool(morsel["httponly"]),
        "sameSite": morsel["samesite"].capitalize(),
        "expires": expires,
    }


@pytest.fixture
def seeded_members() -> dict[Page, SeededMember]:
    return {}


@pytest.fixture
def member_of(seeded_members: dict[Page, SeededMember]) -> Callable[[Page], SeededMember]:
    return seeded_members.__getitem__


@pytest.fixture
def open_person(
    browser: Browser,
    browser_context_args: dict[str, Any],
    e2e_server: RunningServer,
    seeded_members: dict[Page, SeededMember],
) -> Iterator[Callable[..., Page]]:
    contexts: list[BrowserContext] = []

    def open_page(*, signed_in: bool = True, **device_options: Any) -> Page:
        context = browser.new_context(**{**browser_context_args, **device_options})
        contexts.append(context)
        page = context.new_page()
        if signed_in:
            member = seed_member(e2e_server.stores, e2e_server.config)
            context.add_cookies([_playwright_cookie(header, e2e_server.config) for header in member.set_cookie_headers])
            seeded_members[page] = member
        return page

    yield open_page
    for context in contexts:
        context.close()


@pytest.fixture
def page_errors() -> Iterator[list[str]]:
    errors: list[str] = []
    yield errors
    assert errors == []


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


def turn_the_camera_on(person: Page) -> None:
    person.evaluate(PLUG_IN_THE_CAMERA)
    expect(person.locator("#videoBtn")).to_have_attribute("aria-pressed", "true")
    person.click("#videoBtn")
    expect(person.locator("#videoBtn")).to_have_attribute("aria-pressed", "false")


def share_the_screen(person: Page) -> None:
    person.click("#screenBtn")
    expect(person.locator("#screenBtn")).to_have_attribute(
        "aria-pressed", "true", timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS
    )


def stop_the_screen_share(person: Page) -> None:
    person.click("#screenBtn")
    expect(person.locator("#screenBtn")).to_have_attribute(
        "aria-pressed", "false", timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS
    )


def share_then_stop_the_screen_share(person: Page) -> None:
    share_the_screen(person)
    stop_the_screen_share(person)


def wait_for_peer_count(person: Page, count: int) -> None:
    person.wait_for_function(OPEN_PEER_COUNT_REACHED, arg=count, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


def inbound_per_connection(person: Page, kind: str, field: str) -> list[float]:
    return [float(value) for value in person.evaluate(INBOUND_PER_CONNECTION, [kind, field])]


def wait_for_inbound_growth(person: Page, kind: str, field: str, baseline: list[float]) -> None:
    person.wait_for_function(INBOUND_HAS_GROWN, arg=[kind, field, baseline], timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


def wait_for_frames_to_advance(person: Page) -> None:
    wait_for_inbound_growth(person, "video", "framesDecoded", inbound_per_connection(person, "video", "framesDecoded"))


def wait_for_the_picture(person: Page) -> None:
    wait_for_frames_to_advance(person)
    expect(person.locator(ICON_ONLY_ON_THE_TILES)).to_have_count(0, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    expect(person.locator(PICTURE_ONLY_ON_THE_TILES).first).to_be_visible(timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


def expect_the_tile_to_play_audio(person: Page, video_selector: str) -> None:
    person.wait_for_function(
        TILE_AUDIO_IS_ALIVE_AND_UNMUTED, arg=video_selector, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS
    )
    wait_for_inbound_growth(person, "audio", "bytesReceived", inbound_per_connection(person, "audio", "bytesReceived"))
    time_before = person.evaluate(TILE_CURRENT_TIME, video_selector)
    person.wait_for_function(
        TILE_CLOCK_PASSED, arg=[video_selector, time_before], timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS
    )
    level = person.evaluate(
        TILE_AUDIO_LEVEL,
        [video_selector, AUDIBLE_LEVEL_FLOOR, MEDIA_FLOWING_TIMEOUT_MILLISECONDS, AUDIO_SAMPLE_INTERVAL_MILLISECONDS],
    )
    assert level > AUDIBLE_LEVEL_FLOOR, f"the tile plays silence (level {level})"


def expect_both_legal_links(screen: Locator) -> None:
    for label, url in LEGAL_LINKS.items():
        link = screen.get_by_role("link", name=label)
        expect(link).to_be_visible()
        expect(link).to_have_attribute("href", url)
        expect(link).to_have_attribute("target", "_blank")
        expect(link).to_have_attribute("rel", "noopener")


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


def toggle_the_lock(host: Page) -> None:
    open_more(host)
    host.click("#lockBtn")
