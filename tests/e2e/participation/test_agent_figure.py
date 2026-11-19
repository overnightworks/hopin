import base64
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import pytest
from agents.member import SET_TURN_STATE_TOOL, SPEAK_TOOL, PlaceRef
from playwright.sync_api import expect
from speech_client.wire import VoiceId

from hopin.agent.speech_settings import SpeechService
from hopin.agent.tools import CALL_KIND
from hopin.participation.settings import ParticipationSettings
from hopin.participation.vocabulary import CLIP_PATH, FIGURE_MODULE, VOICE_PATH, FigureMood
from tests.e2e.support.client_manifest import entry_of, urls_of_the_chunks_of
from tests.e2e.support.dialed_agent import AGENT_TILE, OWNER_SUBJECT, room_of_link
from tests.e2e.support.media import LOUD, SILENCE, SPEAK_ON_DEMAND
from tests.e2e.support.people import DESKTOP, FORM_FACTORS
from tests.e2e.support.server import RunningServer, loopback_sign_in, running_hopin
from tests.e2e.support.speaker import SPEAKING_TILE, Speaker
from tests.participation.support.figures import SOURCE_PREFIX, Answer, FigureOrigin, digest_of, figure_url, glb, ok
from tests.support.agent_adders import ADDERS_GROUP
from tests.support.loopback_origin import Origin
from tests.support.speech_origin import AUDIENCE_SCOPE, chunked_wav, offering, silent_wav, speaking

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from playwright.sync_api import Page

    from tests.e2e.support.participation_scene import Scene

pytestmark = pytest.mark.e2e

LOADED_FILES = "() => performance.getEntriesByType('resource').map((entry) => entry.name)"
FIGURE_FILES = Path(__file__).parent / "figure_files"
# Both files pass figure v0.8.0's own checks: the figure is its conformant synthetic one, with green arms, and the
# clip holds the right upper arm straight up for eight seconds, far past the length of figure's own wave.
DRAWABLE = (FIGURE_FILES / "drawable.vrm").read_bytes()
HOLDING_WAVE = (FIGURE_FILES / "holding_wave.vrma").read_bytes()
DRAWABLE_URL = figure_url("drawable.vrm")
HOLDING_WAVE_URL = figure_url("holding_wave.vrma")
MISSING_WAVE_URL = figure_url("missing_wave.vrma")
REFUSED_WAVE_URL = figure_url("refused_wave.vrma")
WAVE = "wave"
UNDRAWABLE = glb(b"mozart" * 8)
# The server passes it as a clip, it has a glTF binary header; figure's own check refuses it, it is no animation.
REFUSED_WAVE = glb(b"salieri" * 8)
UNDRAWABLE_URL = figure_url("undrawable.glb")
MISSING_URL = figure_url("missing.glb")


@pytest.fixture(scope="module")
def figure_origin() -> FigureOrigin:
    """The far side of the figure transport: a file the figure cannot draw and a file the source does not hold."""
    origin = FigureOrigin()
    origin.serve(UNDRAWABLE_URL, ok(UNDRAWABLE))
    origin.serve(MISSING_URL, Answer(404))
    origin.serve(DRAWABLE_URL, ok(DRAWABLE))
    origin.serve(HOLDING_WAVE_URL, ok(HOLDING_WAVE))
    origin.serve(MISSING_WAVE_URL, Answer(404))
    origin.serve(REFUSED_WAVE_URL, ok(REFUSED_WAVE))
    return origin


@pytest.fixture(scope="module")
def speech_origin() -> Iterator[Origin]:
    origin = Origin()
    offering(origin, *VoiceId)
    yield origin
    origin.close()


@pytest.fixture(scope="module")
def figure_server(
    tmp_path_factory: pytest.TempPathFactory, figure_origin: FigureOrigin, speech_origin: Origin
) -> Iterator[RunningServer]:
    """HopIn with participation that allows figures from the test source, fetched through `figure_origin`, and lets
    agents speak through `speech_origin`."""
    loopback = loopback_sign_in(tmp_path_factory.mktemp("figure-sign-in"))
    with running_hopin(
        loopback,
        tmp_path_factory.mktemp("figure-tls"),
        participation=True,
        speaks=True,
        speech=SpeechService(speech_origin.base_url, VoiceId.THORSTEN, AUDIENCE_SCOPE),
        agent_adders={OWNER_SUBJECT: ADDERS_GROUP},
        figure_sources=ParticipationSettings(figure_sources=[SOURCE_PREFIX]).figure_sources,
        figure_transport=figure_origin,
    ) as server:
        yield server


@pytest.fixture
def figures_on() -> bool:
    return True


@pytest.fixture
def e2e_server(figures_on: bool, figure_server: RunningServer, participation_server: RunningServer) -> RunningServer:
    return figure_server if figures_on else participation_server


@pytest.fixture
def clock_faked() -> bool:
    return False


@pytest.fixture
def figure_faked() -> bool:
    return False


# Stands in for figure's drawing chunk: it draws nothing and records the id of each element the page hands `listen`,
# null for nobody, and of each sentence the page hands `speak` whether its voice is an audio node and its mood, null for
# none.
FAKE_FIGURE = """
export async function mount(canvas, bytes, options) {
    window.figureListens = [];
    window.figureSentences = [];
    options.onSignal?.("drawn");
    const ignore = () => undefined;
    return {
        signal: "drawn",
        dispose: ignore,
        think: ignore,
        speak: (sentence) => void window.figureSentences.push(
            sentence === null ? null : { voice: sentence.voice instanceof AudioNode, mood: sentence.mood ?? null },
        ),
        gesture: ignore,
        listen: (speaker) => void window.figureListens.push(speaker === null ? null : speaker.id),
    };
}
"""
LISTENS_LAST_TO = "(tileId) => window.figureListens?.at(-1) === tileId"


def figure_drawing_chunk_urls(base_url: str) -> set[str]:
    """The URLs of the page build's files that hold figure's package, without the feature's own chunk."""
    return figure_chunk_urls(base_url) - {f"{base_url}/{entry_of(FIGURE_MODULE)['file']}"}


@pytest.fixture
def clip_answers() -> dict[Page, list[int]]:
    """The statuses each page got for the clips it asked the server for. A faked page clock fakes the page's resource
    timing, so they are taken from the responses."""
    return {}


@pytest.fixture
def open_person(
    open_person: Callable[..., Page],
    clock_faked: bool,
    figure_faked: bool,
    clip_answers: dict[Page, list[int]],
    e2e_server: RunningServer,
) -> Callable[..., Page]:
    """Opens each page recording its clip answers and, when `clock_faked`, with the page clock faked from its first
    load, so the figure's animation keeps one time base when the test later holds the clock; the faked clock runs with
    real time until then. When `figure_faked`, figure's drawing chunk is `FAKE_FIGURE` and the microphone speaks on
    demand."""

    def open_recording(**options: Any) -> Page:
        page = open_person(**options)
        answers = clip_answers.setdefault(page, [])
        page.on(
            "response",
            lambda response: (
                answers.append(response.status) if urlsplit(response.url).path.startswith(CLIP_PATH) else None
            ),
        )
        if clock_faked:
            page.clock.install()
        if figure_faked:
            for url in figure_drawing_chunk_urls(e2e_server.base_url):
                page.route(url, lambda route: route.fulfill(content_type="text/javascript", body=FAKE_FIGURE))
            page.add_init_script(SPEAK_ON_DEMAND)
        return page

    return open_recording


def figure_chunk_urls(base_url: str) -> set[str]:
    """The URLs of the page build's files that hold the figure code: the feature's chunk and the package's."""
    return {f"{base_url}{url}" for url in urls_of_the_chunks_of(FIGURE_MODULE)}


def figure_of(url: str, body: bytes) -> dict[str, str]:
    return {"url": url, "sha256": digest_of(body)}


@pytest.mark.parametrize(
    "figure",
    [
        pytest.param(figure_of(UNDRAWABLE_URL, UNDRAWABLE), id="a file the figure cannot draw"),
        pytest.param(figure_of(MISSING_URL, b""), id="a file the source does not hold"),
    ],
)
def test_a_failed_figure_leaves_the_symbol_and_no_canvas(
    scene_of: Callable[[dict[str, Any]], Scene], e2e_server: RunningServer, figure: dict[str, str]
) -> None:
    """CALL-56: A figure that cannot be drawn or fetched ends, and the tile shows its symbol with no canvas."""
    scene = scene_of(DESKTOP)

    scene.member_adds_and_the_agent_accepts(figure)

    for person in scene.everyone:
        tile = person.locator(AGENT_TILE)
        expect(tile.locator(".vid")).to_have_attribute("data-figure", "ended")
        expect(tile.locator(".av")).to_be_visible()
        expect(tile.locator("canvas")).to_have_count(0)
        assert figure_chunk_urls(e2e_server.base_url) <= set(person.evaluate(LOADED_FILES))


@pytest.mark.parametrize("figures_on", [False])
def test_with_figures_off_no_file_that_holds_the_figure_code_is_requested(
    scene_of: Callable[[dict[str, Any]], Scene], e2e_server: RunningServer
) -> None:
    """CALL-56: Without a figure source the page never requests the figure chunk, whoever has a figure to name."""
    scene = scene_of(DESKTOP)

    scene.member_adds_and_the_agent_accepts(figure_of(UNDRAWABLE_URL, UNDRAWABLE))

    figure_chunks = figure_chunk_urls(e2e_server.base_url)
    assert figure_chunks
    for person in scene.everyone:
        expect(person.locator(AGENT_TILE)).to_have_count(1)
        assert person.locator(AGENT_TILE + " .vid[data-figure]").count() == 0
        assert not figure_chunks & set(person.evaluate(LOADED_FILES))


# The topmost pixel of the arm, as a share of the picture's height: the synthetic figure's arms are the only green in
# the tile, a darker green where the page's controls dim it.
ARM_TOP_SHARE = """
async (base64) => {
    // The page's policy forbids fetching a data URL, so the bytes become a blob without one.
    const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
    const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/png" }));
    const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
    const context = canvas.getContext("2d");
    context.drawImage(bitmap, 0, 0);
    const { data, width, height } = context.getImageData(0, 0, bitmap.width, bitmap.height);
    for (let row = 0; row < height; row++) {
        for (let column = 0; column < width; column++) {
            const [red, green, blue, alpha] = data.subarray(4 * (row * width + column));
            if (alpha >= 200 && green > red + 40 && green > blue + 25) return row / height;
        }
    }
    return null;
}
"""
RAISED_SHARE = 0.1
# Figure's neutral wave lifts the arm's top no higher than a third of the picture, the persona's clip holds it straight
# up and out of the picture's top.
PERSONA_ARM_SHARE = 0.15
WAIT_FOR_THE_FIGURE_MILLISECONDS = 30_000
WAIT_FOR_THE_GESTURE_SECONDS = 30
# The held page clock advances by a step to each picture; a step is far shorter than the wave, so no picture skips it.
STEP_MILLISECONDS = 100
WAVE_MILLISECONDS = 20_000
CLOCK_LEAD_MILLISECONDS = 5_000
# The arm's top at rest moves by the pixel grid and the idle sway, a few thousandths of the picture between two figures;
# an arm still easing back one step before it settles stands about 0.05 off.
REST_TOLERANCE = 0.02
# The arm stands at rest when its top has moved no more than the sway through this many steps in a row; a tile that has
# just been laid out draws at its new size with its first pictures.
SWAY = 0.01
STILL_STEPS = 3
SETTLING_STEPS = 100


def arm_top(person: Page) -> float | None:
    picture = person.locator(f"{AGENT_TILE} canvas").screenshot()
    return person.evaluate(ARM_TOP_SHARE, base64.b64encode(picture).decode())


def persona_clip_plays(person: Page, gesture: Callable[[], object]) -> bool:
    """Whether the persona's clip holds the arm of the person's figure up. A gesture that plays is not started again and
    one made before the clip is parsed plays the neutral wave, so the gesture is made again with each picture."""
    deadline = time.monotonic() + WAIT_FOR_THE_GESTURE_SECONDS
    while time.monotonic() < deadline:
        gesture()
        top = arm_top_after(person, STEP_MILLISECONDS)
        if top is not None and top < PERSONA_ARM_SHARE:
            return True
    return False


def hold_the_page_clock(person: Page) -> None:
    """The page's clock, and with it the figure's animation, stands until the test advances it."""
    person.clock.pause_at(timedelta(milliseconds=CLOCK_LEAD_MILLISECONDS) + datetime.now(tz=UTC))


def arm_top_after(person: Page, milliseconds: int) -> float | None:
    person.clock.run_for(milliseconds)
    return arm_top(person)


def arm_top_seen_after(person: Page, milliseconds: int) -> float:
    top = arm_top_after(person, milliseconds)
    assert top is not None, "the figure is not drawn"
    return top


def held_once_drawn(person: Page) -> None:
    """Holds the page clock the moment the person's tile shows the figure drawn: a figure that draws on in real time
    ends as too slow when a loaded machine draws every browser's figure at once."""
    expect(person.locator(AGENT_TILE).locator(".vid")).to_have_attribute(
        "data-figure", "drawn", timeout=WAIT_FOR_THE_FIGURE_MILLISECONDS
    )
    hold_the_page_clock(person)


def arm_top_at_rest(person: Page) -> float:
    """The arm's top of the figure on the held page clock once it stands still."""
    previous = None
    still = 0
    for _ in range(SETTLING_STEPS):
        top = arm_top_after(person, STEP_MILLISECONDS)
        still = still + 1 if top is not None and previous is not None and abs(top - previous) <= SWAY else 0
        if still == STILL_STEPS and top is not None:
            return top
        previous = top
    pytest.fail(f"the arm did not stand still within {SETTLING_STEPS} steps of the page's clock")


def tops_of_one_wave(person: Page, at_rest: float, gesture: Callable[[], object]) -> list[float]:
    """The tops of the arm in the pictures from the first with the arm raised to the first with the arm drawn at rest
    again, one picture to each step of the held page clock. Fails when the arm is not seen raised before the gesture's
    wait is over, or not back at rest within the clock time of the wave."""
    deadline = time.monotonic() + WAIT_FOR_THE_GESTURE_SECONDS
    top = at_rest
    while top >= at_rest - RAISED_SHARE:
        assert time.monotonic() < deadline, f"the arm was not seen raised within {WAIT_FOR_THE_GESTURE_SECONDS} s"
        gesture()
        top = arm_top_seen_after(person, STEP_MILLISECONDS)
    tops = [top]
    for _ in range(WAVE_MILLISECONDS // STEP_MILLISECONDS):
        top = arm_top_seen_after(person, STEP_MILLISECONDS)
        if abs(top - at_rest) <= REST_TOLERANCE:
            return tops
        tops.append(top)
    pytest.fail(f"the arm did not return to {at_rest} within {WAVE_MILLISECONDS} ms of the page's clock: {tops}")


def everyone_with_the_figure_drawn(scene: Scene, clip: dict[str, str]) -> dict[Page, float]:
    """The persona's figure with `clip` as its wave is accepted and a late joiner is in: each browser of the call with
    its page clock held and the arm's top at rest."""
    figure = figure_of(DRAWABLE_URL, DRAWABLE) | {"gestures": {WAVE: clip}}
    scene.member_adds_and_the_agent_accepts(figure)
    for person in scene.everyone:
        held_once_drawn(person)
    late = scene.person_joins_late()
    held_once_drawn(late)
    return {person: arm_top_at_rest(person) for person in [*scene.everyone, late]}


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
@pytest.mark.parametrize("clock_faked", [True])
def test_a_personas_clip_plays_on_its_figure_in_every_browser_the_late_joiner_included(
    scene_of: Callable[..., Scene], clip_answers: dict[Page, list[int]], form_factor: dict[str, Any]
) -> None:
    """CALL-65: A clip the agent brings plays for its gesture on its tile in every browser of the call, late joiners
    included."""
    scene = scene_of(form_factor)

    everyone = list(everyone_with_the_figure_drawn(scene, figure_of(HOLDING_WAVE_URL, HOLDING_WAVE)))

    assert [persona_clip_plays(person, lambda: scene.agent.gesture(WAVE)) for person in everyone] == [True] * len(
        everyone
    )
    assert [clip_answers[person] for person in everyone] == [[200]] * len(everyone)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
@pytest.mark.parametrize("clock_faked", [True])
@pytest.mark.parametrize(
    ("clip", "answer"),
    [
        pytest.param(figure_of(MISSING_WAVE_URL, b""), 502, id="a clip the server cannot fetch"),
        pytest.param(figure_of(REFUSED_WAVE_URL, REFUSED_WAVE), 200, id="a clip figure refuses"),
    ],
)
def test_a_clip_that_cannot_play_leaves_the_neutral_wave_and_the_figure_drawn(
    scene_of: Callable[..., Scene],
    clip_answers: dict[Page, list[int]],
    form_factor: dict[str, Any],
    clip: dict[str, str],
    answer: int,
) -> None:
    """CALL-65: A clip that cannot be fetched, or that figure refuses, leaves the neutral gesture and the figure is
    drawn all the same."""
    scene = scene_of(form_factor)

    at_rest = everyone_with_the_figure_drawn(scene, clip)
    answers = [clip_answers[person] for person in at_rest]
    waves = [tops_of_one_wave(person, rest, lambda: scene.agent.gesture(WAVE)) for person, rest in at_rest.items()]

    assert answers == [[answer]] * len(at_rest)
    assert min(top for wave in waves for top in wave) > PERSONA_ARM_SHARE


# The tile of a person that rings as speaking on another seat's page, never an agent's.
SPEAKING_PERSON_TILE = "#videoGrid .tile.speaking:not(.agent):not(.self)"
OWN_TILE = "#videoGrid .tile.self"


@pytest.mark.parametrize("figure_faked", [True])
def test_the_figure_turns_toward_the_tile_of_whoever_speaks_on_every_page_and_toward_nobody_in_silence(
    scene_of: Callable[[dict[str, Any]], Scene],
) -> None:
    """CALL-66: While a person speaks, the figure on each seat's page listens to that person's tile, the own tile on
    the speaker's page, and to nobody once they are silent."""
    scene = scene_of(DESKTOP)
    scene.member_adds_and_the_agent_accepts(figure_of(DRAWABLE_URL, DRAWABLE))
    for person in scene.everyone:
        expect(person.locator(AGENT_TILE).locator(".vid")).to_have_attribute("data-figure", "drawn")
    listeners = [scene.host, scene.member]

    scene.owner.evaluate(f"window.speak({LOUD})")

    scene.owner.wait_for_function(LISTENS_LAST_TO, arg=scene.owner.locator(OWN_TILE).get_attribute("id"))
    for person in listeners:
        expect(person.locator(SPEAKING_PERSON_TILE)).to_have_count(1)
        person.wait_for_function(LISTENS_LAST_TO, arg=person.locator(SPEAKING_PERSON_TILE).get_attribute("id"))

    scene.owner.evaluate(f"window.speak({SILENCE})")

    for person in scene.everyone:
        person.wait_for_function(LISTENS_LAST_TO, arg=None)


SENTENCE_SECONDS = 1.0
SPOKEN_WAIT_MILLISECONDS = 15_000


@pytest.mark.parametrize("figure_faked", [True])
def test_the_figure_speaks_an_agents_sentence_with_its_voice_and_mood_on_every_page_and_stops_at_its_end(
    scene_of: Callable[[dict[str, Any]], Scene], speech_origin: Origin
) -> None:
    """CALL-67: While an agent's sentence plays, its figure on every seat's page is handed the sound's audio node and
    the mood the agent named, and once the sentence ended, no sentence."""
    scene = scene_of(DESKTOP)
    scene.member_adds_and_the_agent_accepts(figure_of(DRAWABLE_URL, DRAWABLE))
    for person in scene.everyone:
        expect(person.locator(AGENT_TILE).locator(".vid")).to_have_attribute("data-figure", "drawn")
    speaking(speech_origin, chunked_wav(silent_wav(SENTENCE_SECONDS)))
    place = PlaceRef(kind=CALL_KIND, id=room_of_link(scene.link))

    spoken = scene.agent.call(
        SPEAK_TOOL, {"place": place.model_dump(), "text": "Guten Tag", "mood": FigureMood.HAPPY.value}
    )

    assert spoken == {"spoken": True}
    for person in scene.everyone:
        person.wait_for_function("() => window.figureSentences.length === 2", timeout=SPOKEN_WAIT_MILLISECONDS)
        assert person.evaluate("() => window.figureSentences") == [
            {"voice": True, "mood": FigureMood.HAPPY.value},
            None,
        ]


# Far longer than the test looks at the figure: the sound ends when the agent is told to stop.
LONG_SENTENCE_SECONDS = 120.0
# The thinking look moves the arm far more than the idle sway does.
THINKING_SHIFT = 0.04
THINKING_EASE_MILLISECONDS = 2_000
# A page with its clock held is polled by the clock of the test, not the page's.
POLL_MILLISECONDS = 100


def arm_shifted_from(rest: float, person: Page) -> bool:
    top = arm_top(person)
    return top is not None and abs(top - rest) > THINKING_SHIFT


def wait_until_arm_shifted_from(rest: float, person: Page) -> None:
    """Steps the held page clock until the arm shows the thinking look, as far as the look takes to ease in."""
    for _ in range(SETTLING_STEPS):
        person.clock.run_for(STEP_MILLISECONDS)
        if arm_shifted_from(rest, person):
            return
    pytest.fail("the figure never showed the thinking look")


def arm_stays_while_the_look_eases(rest: float, person: Page) -> bool:
    """Whether the arm stays where it rests through the time the thinking look takes to ease in, on the held clock."""
    for _ in range(THINKING_EASE_MILLISECONDS // STEP_MILLISECONDS):
        person.clock.run_for(STEP_MILLISECONDS)
        if arm_shifted_from(rest, person):
            return False
    return True


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
@pytest.mark.parametrize("clock_faked", [True])
def test_an_agent_that_states_thinking_while_its_sentence_sounds_keeps_speaking_and_thinks_once_it_ended(
    scene_of: Callable[..., Scene],
    speech_origin: Origin,
    form_factor: dict[str, Any],
    e2e_server: RunningServer,
    expected_request_failures: list[str],
) -> None:
    """CALL-68: An agent that states thinking while a sentence of it sounds keeps its speaking tile and its figure
    still, on every seat where the sound plays, and shows thinking once the sound ended; a seat that joined meanwhile,
    where nothing plays, shows it at once. Every page's clock is held, so no figure ends as too slow, and the
    sound outlasts the test's looking, which ends it by stopping the agent."""
    expected_request_failures.append(f"{e2e_server.base_url}{VOICE_PATH}/")
    scene = scene_of(form_factor)
    speaker = Speaker(scene, speech_origin, figure=figure_of(DRAWABLE_URL, DRAWABLE))
    for person in scene.everyone:
        held_once_drawn(person)
    rest = {person: arm_top_at_rest(person) for person in scene.everyone}
    speaker.speak(LONG_SENTENCE_SECONDS)
    for person in scene.everyone:
        expect(person.locator(AGENT_TILE)).to_have_class(SPEAKING_TILE)

    place = PlaceRef(kind=CALL_KIND, id=speaker.room).model_dump()
    scene.agent.call(SET_TURN_STATE_TOOL, {"place": place, "thinking": True})
    still_while_sounding = [arm_stays_while_the_look_eases(rest[person], person) for person in scene.everyone]
    late = scene.person_joins_late()
    held_once_drawn(late)
    wait_until_arm_shifted_from(rest[scene.host], late)

    assert still_while_sounding == [True] * len(scene.everyone)
    for person in scene.everyone:
        expect(person.locator(AGENT_TILE)).to_have_class(SPEAKING_TILE)
    speaker.stop()
    for person in scene.everyone:
        expect(person.locator(AGENT_TILE)).not_to_have_class(SPEAKING_TILE)
        wait_until_arm_shifted_from(rest[person], person)
