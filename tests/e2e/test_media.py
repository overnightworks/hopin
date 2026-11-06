from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.support.calls import add_init_scripts, join, record_page_errors, start_call
from tests.e2e.support.camera_less_call import (
    CAMERA_OFF_AT_JOIN,
    CameraLessCall,
    CameraLessHostCall,
    start_camera_less_host_call,
)
from tests.e2e.support.media import (
    AFTER_PENDING_REJECTIONS_ARE_REPORTED,
    AUDIBLE_LEVEL_FLOOR,
    AUDIO_SAMPLE_INTERVAL_MILLISECONDS,
    COUNT_OFFERS,
    EVERY_VIDEO_CAP_IS,
    ICON_ONLY_ON_THE_TILES,
    MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    OFFER_COUNT,
    ONE_CAP_PER_CONNECTION,
    OTHER_CAMERA_OFF,
    OTHER_VIDEO,
    PICTURE_ONLY_ON_THE_TILES,
    RECORD_MEDIA_TRACKS,
    RECORD_PEER_CONNECTIONS,
    SHARE_THE_SCREEN_FROM_A_CANVAS,
    TILE_AUDIO_IS_ALIVE_AND_UNMUTED,
    TILE_AUDIO_LEVEL,
    TWO_CAPS_PER_CONNECTION,
    VIDEO_TRANSCEIVERS_PER_CONNECTION,
    expect_the_tile_to_play_audio,
    inbound_per_connection,
    share_then_stop_the_screen_share,
    turn_the_camera_on,
    wait_for_inbound_growth,
    wait_for_peer_count,
    wait_for_the_light_to_go_out,
    wait_for_the_picture,
    wait_for_video_tracks,
)
from tests.e2e.support.people import DESKTOP, FORM_FACTORS

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize(
    ("camera_turned_off", "pressed", "tracks_after_the_stop"),
    [(True, "true", ([], [None])), (False, "false", (["live"], ["live"]))],
    ids=["off", "on"],
)
def test_stop_sharing_restores_the_camera_as_the_person_left_it(
    open_person: Callable[..., Page],
    base_url: str,
    page_errors: list[str],
    camera_turned_off: bool,
    pressed: str,
    tracks_after_the_stop: tuple[list[str], list[str | None]],
) -> None:
    """CALL-24: Stopping a share brings the camera back only if it was on before."""
    host = record_page_errors(open_person(**DESKTOP), page_errors)
    add_init_scripts(host, [RECORD_PEER_CONNECTIONS, RECORD_MEDIA_TRACKS])
    watcher = join(open_person, start_call(host, base_url), DESKTOP, (RECORD_PEER_CONNECTIONS,), page_errors)
    wait_for_peer_count(host, 1)
    wait_for_the_picture(watcher)
    if camera_turned_off:
        host.click("#videoBtn")

    share_then_stop_the_screen_share(host)

    expect(host.locator("#videoBtn")).to_have_attribute("aria-pressed", pressed)
    wait_for_video_tracks(host, *tracks_after_the_stop)
    if camera_turned_off:
        wait_for_the_light_to_go_out(host)
        expect(watcher.locator(ICON_ONLY_ON_THE_TILES)).to_have_count(1)
    else:
        wait_for_the_picture(watcher)


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


def _offers_made(call: CameraLessCall) -> list[int]:
    return [call.host.evaluate(OFFER_COUNT), call.guest.evaluate(OFFER_COUNT)]


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_person_without_a_camera_sees_and_hears_everyone_else(camera_less_call: CameraLessCall) -> None:
    """CALL-25: A person without a camera sees and hears everyone else."""
    guest = camera_less_call.guest

    expect(guest.locator(OTHER_VIDEO)).to_be_visible()
    expect(guest.locator(OTHER_CAMERA_OFF)).to_be_hidden()
    wait_for_the_picture(guest)
    expect_the_tile_to_play_audio(guest, OTHER_VIDEO)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_person_without_a_camera_shows_the_icon_and_sends_no_picture_but_is_heard(
    camera_less_call: CameraLessCall,
) -> None:
    """CALL-26: Without a camera nobody receives a picture; the icon shows and the sound is heard."""
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


@pytest.mark.parametrize(
    ("camera_turned_off", "tracks_after_the_stop"),
    [(False, (["live"], ["live"])), (True, ([], [None]))],
    ids=["on", "off"],
)
def test_stop_sharing_keeps_a_camera_added_mid_call_as_the_person_left_it(
    camera_less_call: CameraLessCall, camera_turned_off: bool, tracks_after_the_stop: tuple[list[str], list[str | None]]
) -> None:
    host, guest = camera_less_call.host, camera_less_call.guest
    turn_the_camera_on(guest)
    wait_for_the_picture(host)
    if camera_turned_off:
        guest.click("#videoBtn")

    share_then_stop_the_screen_share(guest)

    wait_for_video_tracks(guest, *tracks_after_the_stop)
    if camera_turned_off:
        wait_for_the_light_to_go_out(guest)
        expect(host.locator(OTHER_CAMERA_OFF)).to_be_visible()
    else:
        wait_for_the_picture(host)


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

    wait_for_video_tracks(host, ["live"], ["live"])
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
    """CALL-25: A camera turned on later reaches the person who was there before and the one who joined after."""
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
