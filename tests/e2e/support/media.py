import time
from typing import TYPE_CHECKING

from playwright.sync_api import Page, expect

from hopin.config.constants import ICE_SERVERS_PATH

if TYPE_CHECKING:
    from collections.abc import Callable

FAKE_MEDIA_BROWSER_ARGS = [
    "--use-fake-device-for-media-stream",
    "--use-fake-ui-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
]
MEDIA_PERMISSIONS = ["camera", "microphone"]
MEDIA_FLOWING_TIMEOUT_MILLISECONDS = 30_000

ICE_SERVERS_URL_PATTERN = f"**{ICE_SERVERS_PATH}"

OTHER_TILE = "#videoGrid .tile:not(.self)"
OTHER_VIDEO = f"{OTHER_TILE} video"
OTHER_CAMERA_OFF = f"{OTHER_TILE} .vid.off"
PICTURE_ONLY_ON_THE_TILES = f"{OTHER_VIDEO}:not(.hidden)"
ICON_ONLY_ON_THE_TILES = f"{OTHER_CAMERA_OFF}:not(.hidden)"

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
POLL_INTERVAL_MILLISECONDS = 50
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
const SCREEN_COLOUR = "rgb(255, 0, 0)";
window.requestedTracks = [];
window.cameraTracks = [];
window.mediaRequests = 0;
window.heldMediaRequests = 0;
const holds = { camera: null, screen: null };
window.holdMedia = (source) => {
    const sources = source ? [source] : Object.keys(holds);
    const hold = new Promise((release, reject) => {
        const lift = () => sources.forEach((held) => { holds[held] = null; });
        window.releaseMedia = () => { lift(); release(); };
        window.rejectMedia = () => { lift(); reject(new DOMException("no camera", "NotFoundError")); };
    });
    sources.forEach((held) => { holds[held] = hold; });
};
const recorded = (source, request) => async (constraints) => {
    window.mediaRequests += 1;
    if (holds[source]) {
        window.heldMediaRequests += 1;
        await holds[source];
    }
    const stream = await request(constraints);
    window.requestedTracks.push(...stream.getTracks());
    if (source === "camera") window.cameraTracks.push(...stream.getVideoTracks());
    return stream;
};
navigator.mediaDevices.getUserMedia = recorded(
    "camera",
    navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices),
);
navigator.mediaDevices.getDisplayMedia = recorded("screen", async () => {
    const canvas = document.createElement("canvas");
    const screenPicture = canvas.captureStream();
    const [screenTrack] = screenPicture.getVideoTracks();
    const repaint = setInterval(() => {
        if (screenTrack.readyState === "ended") return clearInterval(repaint);
        const painter = canvas.getContext("2d");
        painter.fillStyle = SCREEN_COLOUR;
        painter.fillRect(0, 0, canvas.width, canvas.height);
    }, 100);
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
HOLD_CAMERA_REQUESTS = 'window.holdMedia("camera")'
HOLD_SCREEN_REQUESTS = 'window.holdMedia("screen")'
RELEASE_MEDIA = "window.releaseMedia()"
REJECT_MEDIA = "window.rejectMedia()"
MEDIA_REQUEST_COUNT = "() => window.mediaRequests"
MEDIA_REQUEST_IS_HELD = "() => window.heldMediaRequests > 0"
TRACKS_WERE_REQUESTED = "() => window.requestedTracks.length > 0"
MORE_TRACKS_THAN = "(count) => window.requestedTracks.length > count"
REQUESTED_TRACK_COUNT = "() => window.requestedTracks.length"
LIVE_TRACK_COUNT = "() => window.requestedTracks.filter((track) => track.readyState === 'live').length"
CAMERA_TRACK_STATES = "() => window.cameraTracks.map((track) => track.readyState)"
LATE_CAMERA_IS_STOPPED = """
(before) => window.cameraTracks.length > before
    && window.cameraTracks.every((track) => track.readyState === "ended")
"""
VIDEO_TRACKS_NOW = """
() => {
    const own = document.getElementById("localVideo").srcObject?.getVideoTracks() ?? [];
    const senders = window.recordedConnections
        .filter((connection) => connection.signalingState !== "closed")
        .flatMap((connection) => connection.getTransceivers())
        .filter((transceiver) => transceiver.receiver.track.kind === "video")
        .map((transceiver) => transceiver.sender.track);
    const stateOf = (track) => (track.enabled ? track.readyState : `${track.readyState} disabled`);
    return {
        own: own.map(stateOf),
        senders: senders.map((track) => {
            if (!track) return null;
            return own.includes(track) ? stateOf(track) : `foreign ${stateOf(track)}`;
        }),
    };
}
"""
TILE_SHOWS_THE_SCREEN = """
(selector) => {
    const video = document.querySelector(selector);
    if (!video.videoWidth) return false;
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const painter = canvas.getContext("2d");
    painter.drawImage(video, 0, 0, 1, 1);
    const [red, green, blue] = painter.getImageData(0, 0, 1, 1).data;
    return red > 200 && green < 60 && blue < 60;
}
"""
VIDEO_TRACKS_ARE = f"""
(expected) => {{
    const now = ({VIDEO_TRACKS_NOW})();
    return JSON.stringify(now) === JSON.stringify(expected);
}}
"""


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


def wait_for_video_tracks(person: Page, own: list[str], senders: list[str | None]) -> None:
    """Wait until the own stream and every video sender hold exactly these tracks, by `readyState`.

    A disabled track reads "<readyState> disabled". A sender whose track is not in the own stream
    reads "foreign <state>"; no track reads None.
    """
    person.wait_for_function(
        VIDEO_TRACKS_ARE, arg={"own": own, "senders": senders}, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS
    )


def camera_track_states(person: Page) -> list[str]:
    return person.evaluate(CAMERA_TRACK_STATES)


def wait_for_the_light_to_go_out(person: Page) -> None:
    person.wait_for_function(
        "() => window.cameraTracks.every((track) => track.readyState === 'ended')",
        timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    )


def wait_for_the_tile_to_show_the_screen(person: Page) -> None:
    person.wait_for_function(TILE_SHOWS_THE_SCREEN, arg=OTHER_VIDEO, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


def wait_for_peer_count(person: Page, count: int) -> None:
    person.wait_for_function(OPEN_PEER_COUNT_REACHED, arg=count, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


def inbound_per_connection(person: Page, kind: str, field: str) -> list[float]:
    return [float(value) for value in person.evaluate(INBOUND_PER_CONNECTION, [kind, field])]


def poll_until[Measured](
    person: Page,
    measure: Callable[[], Measured],
    reached: Callable[[Measured], bool],
    failure: str,
    deadline_seconds: float | None = None,
) -> Measured:
    # A Python deadline loop: wait_for_function takes the Promise of an async predicate as truthy,
    # and each round trip to the page keeps the mesh relay pumping.
    if deadline_seconds is None:
        deadline_seconds = MEDIA_FLOWING_TIMEOUT_MILLISECONDS / 1000
    deadline = time.monotonic() + deadline_seconds
    while True:
        measured = measure()
        if reached(measured):
            return measured
        last_measured = "" if isinstance(measured, bool) else f"; last measured {measured}"
        assert time.monotonic() < deadline, f"{failure} within {deadline_seconds} s{last_measured}"
        person.wait_for_timeout(POLL_INTERVAL_MILLISECONDS)


def wait_for_inbound_growth(person: Page, kind: str, field: str, baseline: list[float]) -> None:
    poll_until(
        person,
        lambda: inbound_per_connection(person, kind, field),
        lambda now: (
            len(now) == len(baseline) and all(value > before for value, before in zip(now, baseline, strict=True))
        ),
        f"inbound {kind} {field} did not grow past {baseline}",
    )


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
