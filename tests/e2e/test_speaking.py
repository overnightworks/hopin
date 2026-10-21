from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import (
    DESKTOP,
    FORM_FACTORS,
    OTHER_TILE,
    PHONE,
    RECORD_PEER_CONNECTIONS,
    add_init_scripts,
    join,
    record_page_errors,
    start_call,
)

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.e2e

SPEAKING_RING = "rgb(86, 195, 205)"
OTHER_WAVE = f"{OTHER_TILE} .name .wave"
OTHER_MUTE_MARK = f"{OTHER_TILE} .name .muted"
GOES_QUIET_WITHIN_MILLISECONDS = 2_000
OBSERVATION_MILLISECONDS = 2_000
HUM_OBSERVATION_MILLISECONDS = 3_000
PAUSE_MILLISECONDS = 500
WAVE_BAR_COUNT = 3
LOUD = 1
HUM_UNDER_THE_FLOOR = 0.005
SILENCE = 0
DESKTOP_DARK = {**DESKTOP, "color_scheme": "dark"}
DESKTOP_REDUCED_MOTION = {**DESKTOP, "reduced_motion": "reduce"}
# The fake microphone is a steady beep, so a test could not tell speech from quiet.
# This one is an oscillator behind a gain the test sets, and every tile's class changes are logged.
SPEAK_ON_DEMAND = """
(() => {
    const nativeGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    let voice = null;
    window.speak = (gain) => { voice.gain.gain.value = gain; };
    navigator.mediaDevices.getUserMedia = async (constraints) => {
        const stream = await nativeGetUserMedia({ ...constraints, audio: false });
        if (!constraints.audio) return stream;
        if (!voice) {
            const context = new AudioContext();
            const oscillator = context.createOscillator();
            const gain = context.createGain();
            gain.gain.value = 0;
            oscillator.connect(gain);
            oscillator.start();
            voice = { context, gain };
        }
        const destination = voice.context.createMediaStreamDestination();
        voice.gain.connect(destination);
        [window.voiceTrack] = destination.stream.getAudioTracks();
        stream.addTrack(window.voiceTrack);
        return stream;
    };
    window.speakingLog = [];
    const lastSeen = new Map();
    new MutationObserver((records) => {
        for (const { target } of records) {
            const speaking = target.classList.contains("speaking");
            if (target.classList.contains("tile") && speaking !== (lastSeen.get(target.id) ?? false)) {
                lastSeen.set(target.id, speaking);
                window.speakingLog.push({ id: target.id, speaking });
            }
        }
    }).observe(document, { attributes: true, attributeFilter: ["class"], subtree: true });
})();
"""
TILES_THAT_RANG = "() => [...new Set(window.speakingLog.filter((entry) => entry.speaking).map((entry) => entry.id))]"
RING_CHANGES_SO_FAR = """
() => window.speakingLog.map((entry) => entry.speaking).join()
"""
# The floor and the freshness window are speaking.js's own; the page reads them from its source.
SCENARIO_SOUND_IS_ARRIVING = """
async (band) => {
    const source = await (await fetch("/static/js/speaking.js")).text();
    const constant = (name) => Number(source.match(new RegExp(`const ${name} = ([0-9.]+);`))[1]);
    const floor = constant("SPEAKING_LEVEL_FLOOR");
    const freshness = constant("SOUND_FRESHNESS_MS");
    const inBand = {
        loud: (level) => level >= floor,
        hum: (level) => level > 0 && level < floor,
        silent: (level) => level === 0,
    }[band];
    const arrives = () => {
        const now = performance.timeOrigin + performance.now();
        return window.recordedConnections.some((connection) => connection.getReceivers()
            .filter((receiver) => receiver.track.kind === "audio")
            .flatMap((receiver) => receiver.getSynchronizationSources())
            .some((entry) => now - entry.timestamp <= freshness && inBand(entry.audioLevel)));
    };
    const giveUpAt = performance.now() + 10_000;
    while (!arrives()) {
        if (performance.now() > giveUpAt) throw new Error(`no ${band} sound arrived`);
        await new Promise((resolve) => setTimeout(resolve, 20));
    }
}
"""
RING_LOOK = """
(tile) => {
    const ring = getComputedStyle(tile, "::before");
    return {
        shadow: ring.boxShadow,
        animationName: ring.animationName,
        animationDuration: ring.animationDuration,
        display: ring.display,
        opacity: ring.opacity,
    };
}
"""
WAVE_LOOK = """
(wave) => {
    const bars = [...wave.querySelectorAll("i")];
    return {
        barCount: bars.length,
        color: getComputedStyle(bars[0]).backgroundColor,
        barDurations: bars.map((bar) => getComputedStyle(bar).animationDuration),
        beforeTheName: wave.nextSibling.nodeType === Node.TEXT_NODE,
    };
}
"""
DECIDE = """
async ([sources, millisecondsSinceLoud]) => {
    const { isSpeaking } = await import("/static/js/speaking.js");
    const now = 1_000_000;
    const arrived = sources.map(({ audioLevel, age }) => ({ audioLevel, timestamp: now - age }));
    return isSpeaking(arrived, now, now - millisecondsSinceLoud);
}
"""
QUIET_FOR_LONG = 10_000
DECISION_BY_CASE = {
    "loud_and_fresh": ([{"audioLevel": 0.708, "age": 20}], QUIET_FOR_LONG, True),
    "loud_but_stale": ([{"audioLevel": 0.708, "age": 400}], QUIET_FOR_LONG, False),
    "hum_under_the_floor": ([{"audioLevel": 0.005, "age": 20}], QUIET_FOR_LONG, False),
    "no_source": ([], QUIET_FOR_LONG, False),
    "exactly_at_the_floor": ([{"audioLevel": 0.01, "age": 20}], QUIET_FOR_LONG, True),
    "loudest_of_two_sources": ([{"audioLevel": 0, "age": 20}, {"audioLevel": 0.5, "age": 20}], QUIET_FOR_LONG, True),
    "silent_within_the_hold": ([{"audioLevel": 0, "age": 20}], 799, True),
    "silent_after_the_hold": ([{"audioLevel": 0, "age": 20}], 801, False),
}


def host_a_call(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> tuple[Page, str]:
    host = record_page_errors(open_person(**form_factor), page_errors)
    add_init_scripts(host, [SPEAK_ON_DEMAND, RECORD_PEER_CONNECTIONS])
    return host, start_call(host, base_url)


def join_with_a_voice(
    open_person: Callable[..., Page], link: str, page_errors: list[str], form_factor: dict[str, Any] = DESKTOP
) -> Page:
    return join(open_person, link, form_factor, [SPEAK_ON_DEMAND, RECORD_PEER_CONNECTIONS], page_errors)


def start_call_with_guests(
    open_person: Callable[..., Page],
    base_url: str,
    page_errors: list[str],
    guest_count: int = 1,
    form_factor: dict[str, Any] = DESKTOP,
    host_form_factor: dict[str, Any] | None = None,
) -> list[Page]:
    host, link = host_a_call(open_person, base_url, page_errors, host_form_factor or form_factor)
    people = [host]
    for _ in range(guest_count):
        people.append(join_with_a_voice(open_person, link, page_errors, form_factor))
    for person in people:
        expect(person.locator("#stage")).to_have_attribute("data-count", str(len(people)))
    return people


def expect_a_ringing_tile(viewer: Page) -> None:
    expect(viewer.locator(f"{OTHER_TILE}.speaking")).to_have_count(1)
    expect(viewer.locator(f"{OTHER_TILE}.speaking .name .wave")).to_be_visible()


def expect_no_ringing_tile(viewer: Page, sound: str, milliseconds: int = OBSERVATION_MILLISECONDS) -> None:
    # Detection can only ring once the scenario's own sound arrives, so the window opens after it does.
    viewer.evaluate(SCENARIO_SOUND_IS_ARRIVING, sound)
    viewer.wait_for_timeout(milliseconds)
    assert viewer.evaluate(TILES_THAT_RANG) == []


@pytest.mark.parametrize("form_factor", [*FORM_FACTORS, pytest.param(DESKTOP_DARK, id="desktop_dark")])
def test_speaking_rings_the_tile_of_the_guest_who_speaks_with_the_pictures_ring_and_wave(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host, guest, other_guest = start_call_with_guests(open_person, base_url, page_errors, 2, form_factor)
    guest.evaluate(f"window.speak({LOUD})")

    expect_a_ringing_tile(host)
    expect_a_ringing_tile(other_guest)
    assert host.evaluate(TILES_THAT_RANG) == other_guest.evaluate(TILES_THAT_RANG)
    ring = host.locator(f"{OTHER_TILE}.speaking").first.evaluate(RING_LOOK)
    assert SPEAKING_RING in ring["shadow"]
    assert "inset" in ring["shadow"]
    assert "3px" in ring["shadow"]
    assert ring["animationName"] == "speak"
    assert ring["animationDuration"] == "1.8s"
    wave = host.locator(f"{OTHER_TILE}.speaking .name .wave").evaluate(WAVE_LOOK)
    assert wave["barCount"] == WAVE_BAR_COUNT
    assert wave["color"] == SPEAKING_RING
    assert wave["beforeTheName"]
    assert set(wave["barDurations"]) == {"0.9s"}


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_speaking_ring_goes_within_two_seconds_of_silence_and_holds_through_a_half_second_pause(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host, guest = start_call_with_guests(open_person, base_url, page_errors, 1, form_factor)
    guest.evaluate(f"window.speak({LOUD})")
    expect_a_ringing_tile(host)

    guest.evaluate(f"window.speak({SILENCE}); setTimeout(() => window.speak({LOUD}), {PAUSE_MILLISECONDS})")
    host.wait_for_timeout(2 * PAUSE_MILLISECONDS)
    assert host.evaluate(RING_CHANGES_SO_FAR) == "true"

    guest.evaluate(f"window.speak({SILENCE})")
    expect(host.locator(f"{OTHER_TILE}.speaking")).to_have_count(0, timeout=GOES_QUIET_WITHIN_MILLISECONDS)
    expect(host.locator(OTHER_WAVE)).to_be_hidden()


@pytest.mark.parametrize(
    ("form_factor", "ring_display"),
    [pytest.param(DESKTOP, "block", id="desktop"), pytest.param(PHONE, "none", id="phone")],
)
def test_speaking_full_tile_of_two_shows_only_the_wave_on_a_phone(
    open_person: Callable[..., Page],
    base_url: str,
    page_errors: list[str],
    form_factor: dict[str, Any],
    ring_display: str,
) -> None:
    host, guest = start_call_with_guests(open_person, base_url, page_errors, 1, form_factor)
    guest.evaluate(f"window.speak({LOUD})")

    expect_a_ringing_tile(host)
    expect(host.locator(OTHER_WAVE)).to_be_visible()
    assert host.locator(f"{OTHER_TILE}.speaking").evaluate(RING_LOOK)["display"] == ring_display


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_speaking_muted_guest_shows_the_mute_mark_and_never_rings_until_unmuted(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host, guest = start_call_with_guests(open_person, base_url, page_errors, 1, form_factor)
    guest.evaluate(f"window.speak({LOUD})")
    expect_a_ringing_tile(host)

    guest.click("#muteBtn")
    expect(host.locator(OTHER_MUTE_MARK)).to_be_visible()
    expect(host.locator(OTHER_WAVE)).to_be_hidden()
    expect(host.locator(f"{OTHER_TILE}.speaking")).to_have_count(0)
    host.evaluate("window.speakingLog = []")
    expect_no_ringing_tile(host, "silent")

    guest.click("#muteBtn")
    expect_a_ringing_tile(host)


def test_speaking_muted_guest_never_rings_although_loud_sound_still_arrives(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host, guest = start_call_with_guests(open_person, base_url, page_errors)
    guest.click("#muteBtn")
    expect(host.locator(OTHER_MUTE_MARK)).to_be_visible()
    guest.evaluate(f"window.speak({LOUD}); window.voiceTrack.enabled = true")

    expect_no_ringing_tile(host, "loud")
    expect(host.locator(OTHER_MUTE_MARK)).to_be_visible()


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_speaking_host_rings_on_the_guests_page_and_never_on_the_hosts_own_tile(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host, guest = start_call_with_guests(open_person, base_url, page_errors, 1, form_factor)
    host.evaluate(f"window.speak({LOUD})")

    expect_a_ringing_tile(guest)
    expect_no_ringing_tile(host, "silent")
    expect(host.locator("#localContainer .wave")).to_have_count(0)


def test_speaking_guest_with_the_camera_off_rings_around_the_person_icon(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host, guest = start_call_with_guests(open_person, base_url, page_errors)
    guest.click("#videoBtn")
    expect(host.locator(f"{OTHER_TILE} .vid.off")).to_be_visible()
    guest.evaluate(f"window.speak({LOUD})")

    expect_a_ringing_tile(host)


def test_speaking_ring_goes_when_the_guests_sound_stops_arriving(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host, guest = start_call_with_guests(open_person, base_url, page_errors)
    guest.evaluate(f"window.speak({LOUD})")
    expect_a_ringing_tile(host)

    guest.evaluate(
        "Promise.all(window.recordedConnections.flatMap((connection) =>"
        " connection.getSenders().filter((sender) => sender.track?.kind === 'audio')"
        ".map((sender) => sender.replaceTrack(null))))"
    )

    expect(host.locator(f"{OTHER_TILE}.speaking")).to_have_count(0, timeout=GOES_QUIET_WITHIN_MILLISECONDS)


def test_speaking_hum_under_the_floor_never_rings(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host, guest = start_call_with_guests(open_person, base_url, page_errors)
    guest.evaluate(f"window.speak({HUM_UNDER_THE_FLOOR})")

    expect_no_ringing_tile(host, "hum", HUM_OBSERVATION_MILLISECONDS)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_speaking_rings_only_the_tile_of_the_one_who_speaks_among_three(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    host, speaker, listener = start_call_with_guests(open_person, base_url, page_errors, 2, form_factor)
    speaker.evaluate(f"window.speak({LOUD})")

    expect_a_ringing_tile(host)
    expect_a_ringing_tile(listener)
    host.wait_for_timeout(OBSERVATION_MILLISECONDS)
    speakers_tile_on_host = host.evaluate(TILES_THAT_RANG)
    assert len(speakers_tile_on_host) == 1
    assert listener.evaluate(TILES_THAT_RANG) == speakers_tile_on_host
    assert speaker.evaluate(TILES_THAT_RANG) == []


def test_speaking_rings_for_a_guest_who_joins_a_call_in_which_someone_already_speaks(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host, link = host_a_call(open_person, base_url, page_errors, DESKTOP)
    speaker = join_with_a_voice(open_person, link, page_errors)
    speaker.evaluate(f"window.speak({LOUD})")
    expect_a_ringing_tile(host)

    late_joiner = join_with_a_voice(open_person, link, page_errors)

    expect(late_joiner.locator("#stage")).to_have_attribute("data-count", "3")
    expect_a_ringing_tile(late_joiner)


def test_speaking_with_reduced_motion_stands_still_at_full_strength(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    host, guest = start_call_with_guests(open_person, base_url, page_errors, host_form_factor=DESKTOP_REDUCED_MOTION)
    guest.evaluate(f"window.speak({LOUD})")

    expect_a_ringing_tile(host)
    ring = host.locator(f"{OTHER_TILE}.speaking").evaluate(RING_LOOK)
    assert ring["animationDuration"] == "0s"
    assert ring["opacity"] == "1"
    assert set(host.locator(OTHER_WAVE).evaluate(WAVE_LOOK)["barDurations"]) == {"0s"}


@pytest.mark.parametrize(
    ("sources", "milliseconds_since_loud", "speaks"), DECISION_BY_CASE.values(), ids=DECISION_BY_CASE
)
def test_speaking_decision_follows_the_level_its_freshness_and_the_hold(
    open_person: Callable[..., Page],
    base_url: str,
    sources: list[dict[str, float]],
    milliseconds_since_loud: int,
    speaks: bool,
) -> None:
    page = open_person()
    page.goto(f"{base_url}/")

    assert page.evaluate(DECIDE, [sources, milliseconds_since_loud]) is speaks
