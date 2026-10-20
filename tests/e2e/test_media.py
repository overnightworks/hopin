from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import (
    AFTER_PENDING_REJECTIONS_ARE_REPORTED,
    AUDIBLE_LEVEL_FLOOR,
    AUDIO_SAMPLE_INTERVAL_MILLISECONDS,
    COUNT_OFFERS,
    DESKTOP,
    EVERY_VIDEO_CAP_IS,
    FORM_FACTORS,
    ICON_ONLY_ON_THE_TILES,
    MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    OFFER_COUNT,
    ONE_CAP_PER_CONNECTION,
    OTHER_CAMERA_OFF,
    OTHER_VIDEO,
    PICTURE_ONLY_ON_THE_TILES,
    RECORD_PEER_CONNECTIONS,
    REFUSE_THE_CAMERA,
    SHARE_THE_SCREEN_FROM_A_CANVAS,
    TILE_AUDIO_IS_ALIVE_AND_UNMUTED,
    TILE_AUDIO_LEVEL,
    TWO_CAPS_PER_CONNECTION,
    VIDEO_TRANSCEIVERS_PER_CONNECTION,
    expect_the_tile_to_play_audio,
    inbound_per_connection,
    join,
    record_page_errors,
    share_then_stop_the_screen_share,
    start_call,
    turn_the_camera_on,
    wait_for_inbound_growth,
    wait_for_peer_count,
    wait_for_the_picture,
)

pytestmark = pytest.mark.e2e

CAMERA_TRACK_AND_ITS_SENDER_TRACK = """
() => {
    const [localTrack] = document.getElementById("localVideo").srcObject.getVideoTracks();
    const sent = window.recordedConnections
        .flatMap((connection) => connection.getSenders())
        .map((sender) => sender.track)
        .find((track) => track && track.id === localTrack.id);
    return sent ? { local: localTrack.enabled, sent: sent.enabled } : null;
}
"""


@pytest.mark.parametrize(
    ("camera_turned_off", "button_text"),
    [(True, "Cam Off"), (False, "Cam On")],
    ids=["off", "on"],
)
def test_stop_sharing_restores_the_camera_as_the_person_left_it(
    open_person: Callable[..., Page], base_url: str, camera_turned_off: bool, button_text: str
) -> None:
    host = open_person(**DESKTOP)
    host.add_init_script(RECORD_PEER_CONNECTIONS)
    host.add_init_script(SHARE_THE_SCREEN_FROM_A_CANVAS)
    join(open_person, start_call(host, base_url))
    wait_for_peer_count(host, 1)
    if camera_turned_off:
        host.click("#videoBtn")

    share_then_stop_the_screen_share(host)

    expect(host.locator("#videoBtn")).to_have_text(button_text)
    host.wait_for_function(CAMERA_TRACK_AND_ITS_SENDER_TRACK)
    expected = not camera_turned_off
    assert host.evaluate(CAMERA_TRACK_AND_ITS_SENDER_TRACK) == {"local": expected, "sent": expected}


CAMERA_OFF_AT_JOIN = (REFUSE_THE_CAMERA, RECORD_PEER_CONNECTIONS, COUNT_OFFERS, SHARE_THE_SCREEN_FROM_A_CANVAS)
STREAM_IDS_ON_THE_WIRE = r"""
() => {
    const [, ...sections] = window.recordedConnections[0].localDescription.sdp.split("\nm=");
    const streamIdOf = (kind) => sections.find((section) => section.startsWith(kind))?.match(/a=msid:(\S+)/)?.[1];
    return {
        local: document.getElementById("localVideo").srcObject.id,
        audio: streamIdOf("audio"),
        video: streamIdOf("video"),
    };
}
"""
TILE_TRACKS = """
(selector) => document.querySelector(selector).srcObject.getTracks().map((track) => `${track.kind}:${track.readyState}`)
"""
COUNT_TILE_SOURCE_ASSIGNMENTS = """
(() => {
    window.sourceAssignments = 0;
    const native = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "srcObject");
    Object.defineProperty(HTMLMediaElement.prototype, "srcObject", {
        get() { return native.get.call(this); },
        set(stream) {
            if (this.matches("#videoGrid .tile:not(.self) video")) window.sourceAssignments += 1;
            native.set.call(this, stream);
        },
    });
})();
"""
SOURCE_ASSIGNMENTS = "() => window.sourceAssignments"
GIVE_THE_TILE_SILENCE = """
(selector) => {
    const silence = new AudioContext().createMediaStreamDestination();
    document.querySelector(selector).srcObject = silence.stream;
}
"""
GIVE_THE_TILE_A_TONE = """
(selector) => {
    const context = new AudioContext();
    const destination = context.createMediaStreamDestination();
    const tone = context.createOscillator();
    tone.connect(destination);
    tone.start();
    document.querySelector(selector).srcObject = destination.stream;
}
"""
RECORD_THE_AUDIO_LEVEL_WAITS = """
(samplingCostMilliseconds) => {
    window.audioLevelLog = [];
    window.audioContextsOpened = [];
    let virtualNow = 0;
    performance.now = () => virtualNow;
    const nativeSetTimeout = window.setTimeout;
    window.setTimeout = (handler, delay, ...args) => {
        window.audioLevelLog.push({ wait: delay, requestedAt: virtualNow });
        virtualNow += delay;
        return nativeSetTimeout(handler, delay, ...args);
    };
    const nativeSample = AnalyserNode.prototype.getByteTimeDomainData;
    AnalyserNode.prototype.getByteTimeDomainData = function (samples) {
        nativeSample.call(this, samples);
        virtualNow += samplingCostMilliseconds;
        const peak = samples.reduce((loudest, sample) => Math.max(loudest, Math.abs(sample - 128)), 0);
        window.audioLevelLog.push({ sample: peak });
    };
    const NativeAudioContext = window.AudioContext;
    window.AudioContext = class extends NativeAudioContext {
        constructor(...args) {
            super(...args);
            window.audioContextsOpened.push(this);
        }
    };
}
"""
THE_RECORDED_AUDIO_LEVEL_WAITS = """
() => ({
    log: window.audioLevelLog,
    context_states: window.audioContextsOpened.map((context) => context.state),
})
"""
# On the page's virtual clock each loop turn costs the interval plus this, so the deadline falls
# inside a partial final interval and a wait that ignores the remaining budget overruns it
AUDIO_SAMPLING_COST_MILLISECONDS = 5
AUDIO_LEVEL_DEADLINE_MILLISECONDS = 520


@dataclass
class CameraLessCall:
    host: Page
    guest: Page
    invite_link: str


@dataclass
class CameraLessHostCall:
    host: Page
    later_joiner: Page
    invite_link: str


@pytest.fixture
def form_factor() -> dict[str, Any]:
    return DESKTOP


@pytest.fixture
def page_errors() -> Iterator[list[str]]:
    errors: list[str] = []
    yield errors
    assert errors == []


def start_camera_less_call(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any], page_errors: list[str]
) -> CameraLessCall:
    host = record_page_errors(open_person(**form_factor), page_errors)
    host.add_init_script(RECORD_PEER_CONNECTIONS)
    host.add_init_script(COUNT_OFFERS)
    invite_link = start_call(host, base_url)
    guest = join(open_person, invite_link, form_factor, CAMERA_OFF_AT_JOIN, page_errors)
    for person in (host, guest):
        wait_for_peer_count(person, 1)
    return CameraLessCall(host, guest, invite_link)


@pytest.fixture
def camera_less_call(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any], page_errors: list[str]
) -> CameraLessCall:
    return start_camera_less_call(open_person, base_url, form_factor, page_errors)


def start_camera_less_host_call(
    open_person: Callable[..., Page],
    base_url: str,
    form_factor: dict[str, Any],
    page_errors: list[str],
    host_init_scripts: tuple[str, ...] = (),
) -> CameraLessHostCall:
    host = record_page_errors(open_person(**form_factor), page_errors)
    for script in (RECORD_PEER_CONNECTIONS, COUNT_OFFERS, *host_init_scripts):
        host.add_init_script(script)
    invite_link = start_call(host, base_url, with_camera=False)
    later_joiner = join(open_person, invite_link, form_factor, (RECORD_PEER_CONNECTIONS, COUNT_OFFERS), page_errors)
    for person in (host, later_joiner):
        wait_for_peer_count(person, 1)
    return CameraLessHostCall(host, later_joiner, invite_link)


@pytest.fixture
def camera_less_host_call(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any], page_errors: list[str]
) -> CameraLessHostCall:
    return start_camera_less_host_call(open_person, base_url, form_factor, page_errors)


def _offers_made(call: CameraLessCall) -> list[int]:
    return [call.host.evaluate(OFFER_COUNT), call.guest.evaluate(OFFER_COUNT)]


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_person_without_a_camera_sees_and_hears_everyone_else(camera_less_call: CameraLessCall) -> None:
    guest = camera_less_call.guest

    expect(guest.locator(OTHER_VIDEO)).to_be_visible()
    expect(guest.locator(OTHER_CAMERA_OFF)).to_be_hidden()
    wait_for_the_picture(guest)
    expect_the_tile_to_play_audio(guest, OTHER_VIDEO)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_person_without_a_camera_shows_the_icon_and_sends_no_picture_but_is_heard(
    camera_less_call: CameraLessCall,
) -> None:
    host = camera_less_call.host

    expect(host.locator(OTHER_CAMERA_OFF)).to_be_visible()
    expect_the_tile_to_play_audio(host, OTHER_VIDEO)
    assert inbound_per_connection(host, "video", "framesDecoded") == [0]


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_camera_turned_on_mid_call_reaches_the_people_who_were_there_under_the_cap_without_an_offer(
    camera_less_call: CameraLessCall,
) -> None:
    host, guest = camera_less_call.host, camera_less_call.guest
    offers_before = _offers_made(camera_less_call)

    turn_the_camera_on(guest)

    expect(host.locator(OTHER_CAMERA_OFF)).to_be_hidden()
    wait_for_the_picture(host)
    expect_the_tile_to_play_audio(host, OTHER_VIDEO)
    expect_the_tile_to_play_audio(guest, OTHER_VIDEO)
    assert _offers_made(camera_less_call) == offers_before
    guest.wait_for_function(EVERY_VIDEO_CAP_IS, arg=ONE_CAP_PER_CONNECTION, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_picture_arrives_under_the_stream_of_the_voice(camera_less_call: CameraLessCall) -> None:
    host, guest = camera_less_call.host, camera_less_call.guest

    turn_the_camera_on(guest)

    expect(host.locator(OTHER_CAMERA_OFF)).to_be_hidden()
    assert sorted(host.evaluate(TILE_TRACKS, OTHER_VIDEO)) == ["audio:live", "video:live"]
    stream_ids = guest.evaluate(STREAM_IDS_ON_THE_WIRE)
    assert stream_ids["audio"] == stream_ids["video"] == stream_ids["local"]


def test_the_tile_gets_the_voice_and_then_the_picture_each_assigned_once(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host = record_page_errors(open_person(**DESKTOP), page_errors)
    host.add_init_script(RECORD_PEER_CONNECTIONS)
    host.add_init_script(COUNT_TILE_SOURCE_ASSIGNMENTS)
    invite_link = start_call(host, base_url)
    guest = join(open_person, invite_link, DESKTOP, CAMERA_OFF_AT_JOIN, page_errors)
    wait_for_peer_count(host, 1)
    host.wait_for_function(TILE_AUDIO_IS_ALIVE_AND_UNMUTED, arg=OTHER_VIDEO)
    assigned_for_the_voice = host.evaluate(SOURCE_ASSIGNMENTS)
    assert assigned_for_the_voice == 1

    turn_the_camera_on(guest)
    expect(host.locator(PICTURE_ONLY_ON_THE_TILES)).to_be_visible()
    wait_for_the_picture(host)

    assert host.evaluate(SOURCE_ASSIGNMENTS) == assigned_for_the_voice + 1


def test_a_camera_turned_on_before_a_late_joiner_arrives_reaches_the_late_joiner_under_the_cap(
    camera_less_call: CameraLessCall, open_person: Callable[..., Page], page_errors: list[str]
) -> None:
    guest = camera_less_call.guest
    turn_the_camera_on(guest)
    wait_for_the_picture(camera_less_call.host)

    late_joiner = join(
        open_person,
        camera_less_call.invite_link,
        init_scripts=(RECORD_PEER_CONNECTIONS,),
        page_errors=page_errors,
    )

    wait_for_peer_count(late_joiner, 2)
    expect(late_joiner.locator(PICTURE_ONLY_ON_THE_TILES)).to_have_count(2)
    expect(late_joiner.locator(ICON_ONLY_ON_THE_TILES)).to_have_count(0)
    wait_for_the_picture(late_joiner)
    guest.wait_for_function(EVERY_VIDEO_CAP_IS, arg=TWO_CAPS_PER_CONNECTION, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


def test_a_camera_turned_off_and_on_again_shows_the_icon_then_the_picture_without_an_offer(
    camera_less_call: CameraLessCall,
) -> None:
    host, guest = camera_less_call.host, camera_less_call.guest
    turn_the_camera_on(guest)
    wait_for_the_picture(host)
    offers_before = _offers_made(camera_less_call)

    guest.click("#videoBtn")

    expect(host.locator(OTHER_CAMERA_OFF)).to_be_visible()
    assert guest.evaluate(CAMERA_TRACK_AND_ITS_SENDER_TRACK) == {"local": False, "sent": False}
    guest.click("#videoBtn")
    expect(host.locator(OTHER_CAMERA_OFF)).to_be_hidden()
    wait_for_the_picture(host)
    assert _offers_made(camera_less_call) == offers_before


@pytest.mark.parametrize(
    ("camera_turned_off", "sent_state"),
    [(False, {"local": True, "sent": True}), (True, {"local": False, "sent": False})],
    ids=["on", "off"],
)
def test_stop_sharing_keeps_a_camera_added_mid_call_as_the_person_left_it(
    camera_less_call: CameraLessCall, camera_turned_off: bool, sent_state: dict[str, bool]
) -> None:
    host, guest = camera_less_call.host, camera_less_call.guest
    turn_the_camera_on(guest)
    wait_for_the_picture(host)
    if camera_turned_off:
        guest.click("#videoBtn")

    share_then_stop_the_screen_share(guest)

    guest.wait_for_function(CAMERA_TRACK_AND_ITS_SENDER_TRACK)
    assert guest.evaluate(CAMERA_TRACK_AND_ITS_SENDER_TRACK) == sent_state
    if camera_turned_off:
        expect(host.locator(OTHER_CAMERA_OFF)).to_be_visible()
    else:
        wait_for_the_picture(host)


OFFER_THE_VIDEO_DIRECTION = """
async (direction) => {
    const connection = window.recordedConnections[0];
    connection.getTransceivers()
        .filter((transceiver) => transceiver.receiver.track.kind === "video")
        .forEach((transceiver) => { transceiver.direction = direction; });
    await connection.setLocalDescription(await connection.createOffer());
    return connection.localDescription.sdp;
}
"""
ANSWER_THE_OFFER = """
async (sdp) => {
    const connection = window.recordedConnections[0];
    await connection.setRemoteDescription({ type: "offer", sdp });
    await connection.setLocalDescription(await connection.createAnswer());
    return connection.localDescription.sdp;
}
"""
ACCEPT_THE_ANSWER = "(sdp) => window.recordedConnections[0].setRemoteDescription({ type: 'answer', sdp })"


def renegotiate_the_video_direction(offerer: Page, answerer: Page, direction: str) -> None:
    offer = offerer.evaluate(OFFER_THE_VIDEO_DIRECTION, direction)
    offerer.evaluate(ACCEPT_THE_ANSWER, answerer.evaluate(ANSWER_THE_OFFER, offer))


def test_a_video_track_that_mutes_without_a_camera_off_announcement_shows_the_icon_until_it_unmutes(
    camera_less_call: CameraLessCall,
) -> None:
    host, guest = camera_less_call.host, camera_less_call.guest
    turn_the_camera_on(guest)
    expect(host.locator(PICTURE_ONLY_ON_THE_TILES)).to_be_visible()

    renegotiate_the_video_direction(guest, host, "inactive")
    expect(host.locator(ICON_ONLY_ON_THE_TILES)).to_be_visible()
    expect(host.locator(PICTURE_ONLY_ON_THE_TILES)).to_have_count(0)
    expect(guest.locator("#videoBtn")).to_have_text("Cam On")

    renegotiate_the_video_direction(guest, host, "sendrecv")
    expect(host.locator(PICTURE_ONLY_ON_THE_TILES)).to_be_visible(timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    expect(host.locator(ICON_ONLY_ON_THE_TILES)).to_have_count(0)


def _measure_the_audio_level_waits(person: Page, give_the_tile_audio: str) -> tuple[float, dict[str, Any]]:
    person.set_content("<video id='tile'></video>")
    person.evaluate("() => { window.requestAnimationFrame = () => 0; }")
    person.evaluate(give_the_tile_audio, "#tile")
    person.evaluate(RECORD_THE_AUDIO_LEVEL_WAITS, AUDIO_SAMPLING_COST_MILLISECONDS)
    level = person.evaluate(
        TILE_AUDIO_LEVEL,
        ["#tile", AUDIBLE_LEVEL_FLOOR, AUDIO_LEVEL_DEADLINE_MILLISECONDS, AUDIO_SAMPLE_INTERVAL_MILLISECONDS],
    )
    return level, person.evaluate(THE_RECORDED_AUDIO_LEVEL_WAITS)


def test_the_audio_level_wait_over_silence_requests_no_more_waiting_than_its_deadline_and_closes_its_context(
    open_person: Callable[..., Page], page_errors: list[str]
) -> None:
    person = record_page_errors(open_person(**DESKTOP), page_errors)

    level, recorded = _measure_the_audio_level_waits(person, GIVE_THE_TILE_SILENCE)

    assert level <= AUDIBLE_LEVEL_FLOOR
    waits = [entry for entry in recorded["log"] if "wait" in entry]
    assert waits
    assert all(entry["requestedAt"] + entry["wait"] <= AUDIO_LEVEL_DEADLINE_MILLISECONDS for entry in waits)
    assert waits[-1]["wait"] < AUDIO_SAMPLE_INTERVAL_MILLISECONDS
    assert recorded["context_states"] == ["closed"]
    assert page_errors == []


def test_the_audio_level_wait_over_a_tone_requests_no_waiting_after_the_loud_sample_and_closes_its_context(
    open_person: Callable[..., Page], page_errors: list[str]
) -> None:
    person = record_page_errors(open_person(**DESKTOP), page_errors)

    level, recorded = _measure_the_audio_level_waits(person, GIVE_THE_TILE_A_TONE)

    assert level > AUDIBLE_LEVEL_FLOOR
    assert recorded["log"][-1] == {"sample": level}
    assert recorded["context_states"] == ["closed"]
    assert page_errors == []


VIDEO_SECTION_DIRECTION = r"""
() => {
    const [, ...sections] = window.recordedConnections[0].localDescription.sdp.split("\nm=");
    return sections.find((section) => section.startsWith("video")).match(/a=(sendrecv|recvonly|sendonly|inactive)/)[1];
}
"""
DELETE_SET_STREAMS = "delete RTCRtpSender.prototype.setStreams;"
A_VIDEO_SENDER_HOLDS_A_TRACK = """
() => window.recordedConnections.some((connection) => connection.getSenders()
    .some((sender) => sender.track?.kind === "video"))
"""
OUTBOUND_VIDEO_BYTES_SENT = """
async () => {
    const reports = await Promise.all(window.recordedConnections.map((connection) => connection.getStats()));
    return reports.flatMap((stats) => [...stats.values()])
        .filter((report) => report.type === "outbound-rtp" && report.kind === "video")
        .reduce((total, report) => total + report.bytesSent, 0);
}
"""
HOLD_OFFERS_BEING_APPLIED = """
window.heldOffers = 0;
window.offerGate = new Promise((release) => { window.releaseOffers = release; });
const nativeSetRemoteDescription = RTCPeerConnection.prototype.setRemoteDescription;
RTCPeerConnection.prototype.setRemoteDescription = function (description) {
    if (description.type !== "offer") return nativeSetRemoteDescription.call(this, description);
    window.heldOffers += 1;
    return window.offerGate.then(() => nativeSetRemoteDescription.call(this, description));
};
"""
HOLD_THE_ANSWER_HAND_OVER = """
window.heldHandOvers = 0;
window.settledHandOvers = 0;
window.handOverGate = new Promise((release) => { window.releaseHandOvers = release; });
const nativeReplaceTrack = RTCRtpSender.prototype.replaceTrack;
RTCRtpSender.prototype.replaceTrack = function (track) {
    window.heldHandOvers += 1;
    const replaced = nativeReplaceTrack.call(this, track);
    const countSettled = () => { window.settledHandOvers += 1; };
    replaced.then(countSettled, countSettled);
    return window.handOverGate.then(() => replaced);
};
"""
HAND_OVER_HELD = "() => window.heldHandOvers > 0"
HAND_OVER_SETTLED = "() => window.settledHandOvers === 1"
RELEASE_THE_HAND_OVER = "() => window.releaseHandOvers()"
HELD_OFFERS_REACHED = "(count) => window.heldOffers === count"
RELEASE_THE_HELD_OFFERS = "() => window.releaseOffers()"
EVERY_TILE_VIDEO = (
    "() => [...document.querySelectorAll('#videoGrid .tile:not(.self) video')].map((video) => `#${video.id}`)"
)


def _expect_every_tile_to_play_audio(person: Page) -> None:
    for selector in person.evaluate(EVERY_TILE_VIDEO):
        expect_the_tile_to_play_audio(person, selector)


def _offers_counted(*people: Page) -> list[int]:
    return [person.evaluate(OFFER_COUNT) for person in people]


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_person_without_a_camera_sees_and_hears_the_one_who_joined_later(
    camera_less_host_call: CameraLessHostCall,
) -> None:
    host = camera_less_host_call.host

    wait_for_the_picture(host)
    expect_the_tile_to_play_audio(host, OTHER_VIDEO)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_person_without_a_camera_shows_the_icon_to_the_one_who_joined_later_and_sends_no_picture(
    camera_less_host_call: CameraLessHostCall,
) -> None:
    later_joiner = camera_less_host_call.later_joiner

    expect_the_tile_to_play_audio(later_joiner, OTHER_VIDEO)

    expect(later_joiner.locator(ICON_ONLY_ON_THE_TILES)).to_be_visible()
    expect(later_joiner.locator(PICTURE_ONLY_ON_THE_TILES)).to_have_count(0)
    assert inbound_per_connection(later_joiner, "video", "framesDecoded") == [0]


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_camera_turned_on_by_the_answering_side_reaches_the_one_who_joined_later_under_the_cap_without_an_offer(
    camera_less_host_call: CameraLessHostCall,
) -> None:
    host, later_joiner = camera_less_host_call.host, camera_less_host_call.later_joiner
    offers_before = _offers_counted(host, later_joiner)

    turn_the_camera_on(host)

    wait_for_the_picture(later_joiner)
    expect_the_tile_to_play_audio(later_joiner, OTHER_VIDEO)
    expect_the_tile_to_play_audio(host, OTHER_VIDEO)
    assert _offers_counted(host, later_joiner) == offers_before
    host.wait_for_function(EVERY_VIDEO_CAP_IS, arg=ONE_CAP_PER_CONNECTION, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_answered_picture_arrives_under_the_stream_of_the_voice_in_a_sendrecv_section(
    camera_less_host_call: CameraLessHostCall,
) -> None:
    host, later_joiner = camera_less_host_call.host, camera_less_host_call.later_joiner

    turn_the_camera_on(host)

    expect(later_joiner.locator(OTHER_CAMERA_OFF)).to_be_hidden()
    assert sorted(later_joiner.evaluate(TILE_TRACKS, OTHER_VIDEO)) == ["audio:live", "video:live"]
    assert host.evaluate(VIDEO_SECTION_DIRECTION) == "sendrecv"
    stream_ids = host.evaluate(STREAM_IDS_ON_THE_WIRE)
    assert stream_ids["audio"] == stream_ids["video"] == stream_ids["local"]


def test_stop_sharing_keeps_the_added_camera_of_the_answering_side_for_the_one_who_joined_later(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    call = start_camera_less_host_call(open_person, base_url, DESKTOP, page_errors, (SHARE_THE_SCREEN_FROM_A_CANVAS,))
    host, later_joiner = call.host, call.later_joiner
    turn_the_camera_on(host)
    wait_for_the_picture(later_joiner)

    share_then_stop_the_screen_share(host)

    wait_for_the_picture(later_joiner)
    host.wait_for_function(EVERY_VIDEO_CAP_IS, arg=ONE_CAP_PER_CONNECTION, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    for person in (host, later_joiner):
        assert person.evaluate(VIDEO_TRANSCEIVERS_PER_CONNECTION) == [1]


def test_where_streams_cannot_be_set_the_answering_side_sends_no_picture_and_the_call_stays_as_it_was(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    call = start_camera_less_host_call(open_person, base_url, DESKTOP, page_errors, (DELETE_SET_STREAMS,))
    host, later_joiner = call.host, call.later_joiner
    wait_for_the_picture(host)
    expect_the_tile_to_play_audio(host, OTHER_VIDEO)
    expect_the_tile_to_play_audio(later_joiner, OTHER_VIDEO)

    turn_the_camera_on(host)
    host.wait_for_function(A_VIDEO_SENDER_HOLDS_A_TRACK)
    wait_for_inbound_growth(
        later_joiner, "audio", "bytesReceived", inbound_per_connection(later_joiner, "audio", "bytesReceived")
    )

    assert inbound_per_connection(later_joiner, "video", "framesDecoded") == [0]
    assert host.evaluate(OUTBOUND_VIDEO_BYTES_SENT) == 0
    expect(later_joiner.locator(ICON_ONLY_ON_THE_TILES)).to_be_visible()
    for person in (host, later_joiner):
        assert person.evaluate(VIDEO_TRANSCEIVERS_PER_CONNECTION) == [1]


def test_a_camera_adopted_while_the_offer_is_being_applied_reaches_the_one_who_joined_later(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    call = start_camera_less_host_call(open_person, base_url, DESKTOP, page_errors, (HOLD_OFFERS_BEING_APPLIED,))
    host, later_joiner = call.host, call.later_joiner
    host.wait_for_function(HELD_OFFERS_REACHED, arg=1, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

    turn_the_camera_on(host)
    host.evaluate(RELEASE_THE_HELD_OFFERS)

    wait_for_the_picture(later_joiner)
    host.wait_for_function(EVERY_VIDEO_CAP_IS, arg=ONE_CAP_PER_CONNECTION, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


def test_a_camera_turned_on_in_a_call_of_three_reaches_the_one_who_was_there_before_and_the_one_who_joined_later(
    camera_less_call: CameraLessCall, open_person: Callable[..., Page], page_errors: list[str]
) -> None:
    host, guest = camera_less_call.host, camera_less_call.guest
    later_joiner = join(
        open_person,
        camera_less_call.invite_link,
        init_scripts=(RECORD_PEER_CONNECTIONS, COUNT_OFFERS),
        page_errors=page_errors,
    )
    for person in (host, guest, later_joiner):
        wait_for_peer_count(person, 2)
    offers_before = _offers_counted(host, guest, later_joiner)

    turn_the_camera_on(guest)

    for person in (host, later_joiner):
        wait_for_the_picture(person)
        _expect_every_tile_to_play_audio(person)
    assert _offers_counted(host, guest, later_joiner) == offers_before
    guest.wait_for_function(EVERY_VIDEO_CAP_IS, arg=TWO_CAPS_PER_CONNECTION, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


def test_leaving_while_the_answer_hand_over_is_pending_raises_no_page_error(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    call = start_camera_less_host_call(open_person, base_url, DESKTOP, page_errors, (HOLD_THE_ANSWER_HAND_OVER,))
    host, later_joiner = call.host, call.later_joiner
    host.wait_for_function(HAND_OVER_HELD, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

    later_joiner.click("#leaveBtn")
    wait_for_peer_count(host, 0)
    host.evaluate(RELEASE_THE_HAND_OVER)
    host.wait_for_function(HAND_OVER_SETTLED, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)

    host.evaluate(AFTER_PENDING_REJECTIONS_ARE_REPORTED)


WATCH_THE_TILE_PICTURE = """
([selector, sampleIntervalMilliseconds, pictureFloor]) => {
    const video = document.querySelector(selector);
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d", { willReadFrequently: true });
    const drawsAPicture = () => {
        context.clearRect(0, 0, canvas.width, canvas.height);
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
        const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
        let brightness = 0;
        for (let index = 0; index < pixels.length; index += 4) {
            brightness += pixels[index] + pixels[index + 1] + pixels[index + 2];
        }
        return brightness / (canvas.width * canvas.height * 3) > pictureFloor;
    };
    const showsTheVideo = () => !video.classList.contains("hidden");
    const watch = { shown: [], samples: [] };
    new MutationObserver(() => {
        if (showsTheVideo()) watch.shown.push(drawsAPicture());
    }).observe(video, { attributeFilter: ["class"] });
    watch.sampler = setInterval(
        () => watch.samples.push({ shows: showsTheVideo(), picture: drawsAPicture() }),
        sampleIntervalMilliseconds,
    );
    window.tilePictureWatch = watch;
}
"""
LAST_SAMPLES_SHOW_THE_PICTURE = """
(count) => {
    const last = window.tilePictureWatch.samples.slice(-count);
    return last.length === count && last.every((sample) => sample.shows && sample.picture);
}
"""
STOP_THE_TILE_PICTURE_WATCH = """
() => {
    const watch = window.tilePictureWatch;
    clearInterval(watch.sampler);
    return { shown: watch.shown, samples: watch.samples };
}
"""
TILE_SAMPLE_INTERVAL_MILLISECONDS = 5
# Mean channel value of the drawn tile; an empty canvas and a black frame both stay at or below it
PICTURE_BRIGHTNESS_FLOOR = 16
LAST_SAMPLES_WITH_THE_PICTURE = 3
# A page whose timers are throttled to once a second takes fewer samples than this before the picture settles
MINIMUM_TILE_SAMPLES = 10


@dataclass
class WatchedCameraLessPerson:
    watcher: Page
    person: Page


@dataclass
class TilePictureWatch:
    shown: list[bool]
    samples: list[dict[str, bool]]


def _the_host_watches_the_guest(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any], page_errors: list[str]
) -> WatchedCameraLessPerson:
    call = start_camera_less_call(open_person, base_url, form_factor, page_errors)
    return WatchedCameraLessPerson(watcher=call.host, person=call.guest)


def _the_later_joiner_watches_the_host(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any], page_errors: list[str]
) -> WatchedCameraLessPerson:
    call = start_camera_less_host_call(open_person, base_url, form_factor, page_errors)
    return WatchedCameraLessPerson(watcher=call.later_joiner, person=call.host)


@pytest.fixture(
    params=[
        pytest.param(_the_host_watches_the_guest, id="offered"),
        pytest.param(_the_later_joiner_watches_the_host, id="answered"),
    ]
)
def watched_camera_less_person(
    request: pytest.FixtureRequest,
    open_person: Callable[..., Page],
    base_url: str,
    form_factor: dict[str, Any],
    page_errors: list[str],
) -> WatchedCameraLessPerson:
    start_the_watched_call: Callable[..., WatchedCameraLessPerson] = request.param
    return start_the_watched_call(open_person, base_url, form_factor, page_errors)


def _watch_the_tile_picture(watcher: Page) -> None:
    watcher.bring_to_front()
    watcher.evaluate(WATCH_THE_TILE_PICTURE, [OTHER_VIDEO, TILE_SAMPLE_INTERVAL_MILLISECONDS, PICTURE_BRIGHTNESS_FLOOR])


def _stop_the_tile_picture_watch_once_the_picture_settles(watcher: Page) -> TilePictureWatch:
    watcher.wait_for_function(
        LAST_SAMPLES_SHOW_THE_PICTURE, arg=LAST_SAMPLES_WITH_THE_PICTURE, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS
    )
    return TilePictureWatch(**watcher.evaluate(STOP_THE_TILE_PICTURE_WATCH))


def _expect_the_video_shown_only_with_a_picture(watch: TilePictureWatch) -> None:
    assert watch.shown, "the tile never turned to its video while watched"
    assert all(watch.shown), f"the tile showed its video before it could draw a frame: {watch.shown}"
    assert len(watch.samples) >= MINIMUM_TILE_SAMPLES, f"only {len(watch.samples)} samples"
    shown_samples = [sample["picture"] for sample in watch.samples if sample["shows"]]
    assert all(shown_samples), f"a sample showed the video without a picture: {shown_samples}"


def test_a_camera_turned_on_mid_call_turns_the_icon_into_the_picture_with_no_empty_frame_between(
    watched_camera_less_person: WatchedCameraLessPerson,
) -> None:
    watcher = watched_camera_less_person.watcher
    _watch_the_tile_picture(watcher)

    turn_the_camera_on(watched_camera_less_person.person)

    wait_for_the_picture(watcher)
    _expect_the_video_shown_only_with_a_picture(_stop_the_tile_picture_watch_once_the_picture_settles(watcher))
