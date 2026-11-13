import re
from typing import TYPE_CHECKING, Any

import pytest
from agents.member import SPEAK_TOOL, PlaceRef
from playwright.sync_api import Page, expect
from speech_client.wire import VoiceId

from hopin.agent.speech_settings import SpeechService
from hopin.agent.tools import CALL_KIND, LEAVE_CALL
from hopin.participation.speaking import MILLISECONDS_PER_SECOND
from tests.e2e.support.dialed_agent import ADDERS_GROUP, AGENT_TILE, OWNER_SUBJECT, REQUEST_SECONDS, room_of_link
from tests.e2e.support.people import FORM_FACTORS
from tests.e2e.support.server import RunningServer, loopback_sign_in, running_hopin
from tests.support.loopback_origin import Origin
from tests.support.speech_origin import AUDIENCE_SCOPE, chunked_wav, silent_wav, speaking

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from concurrent.futures import Future

    from tests.e2e.support.dialed_agent import OwnAccountAgent
    from tests.e2e.support.participation_scene import Scene

pytestmark = pytest.mark.e2e

SPEAKING_TILE = re.compile(r"\bspeaking\b")
SOUND_SECONDS = 1.0
LONGER_SOUND_SECONDS = 5.0
LATER_SOUND_SECONDS = (1.5, 1.25)
PLAYBACK_WAIT_MILLISECONDS = 15_000
# Each page records the length of every sound its player starts and how many it stopped.
RECORD_PLAYBACK = """
() => {
    window.soundsStarted = [];
    window.soundsStopped = 0;
    const start = AudioBufferSourceNode.prototype.start;
    AudioBufferSourceNode.prototype.start = function (...args) {
        window.soundsStarted.push(this.buffer.duration);
        return start.apply(this, args);
    };
    const stop = AudioBufferSourceNode.prototype.stop;
    AudioBufferSourceNode.prototype.stop = function (...args) {
        window.soundsStopped += 1;
        return stop.apply(this, args);
    };
}
"""


@pytest.fixture(scope="module")
def speech_origin() -> Iterator[Origin]:
    origin = Origin()
    yield origin
    origin.close()


@pytest.fixture(scope="module")
def voicing_server(tmp_path_factory: pytest.TempPathFactory, speech_origin: Origin) -> Iterator[RunningServer]:
    """HopIn with participation and the setting that lets agents speak on, speaking through `speech_origin`."""
    loopback = loopback_sign_in(tmp_path_factory.mktemp("voice-sign-in"))
    with running_hopin(
        loopback,
        tmp_path_factory.mktemp("voice-tls"),
        participation=True,
        speaks=True,
        speech=SpeechService(speech_origin.base_url, VoiceId.THORSTEN, AUDIENCE_SCOPE),
        agent_adders={OWNER_SUBJECT: ADDERS_GROUP},
    ) as server:
        yield server


@pytest.fixture
def e2e_server(voicing_server: RunningServer) -> RunningServer:
    return voicing_server


class Speaker:
    """The call of three people whose member added the owner's agent, a recording player on each page, and the agent's
    sentences."""

    def __init__(self, scene: Scene, speech_origin: Origin, *, other: OwnAccountAgent | None = None) -> None:
        self.scene = scene
        self.speech_origin = speech_origin
        self.room = room_of_link(scene.link)
        scene.member_adds_and_the_agent_accepts()
        if other is not None:
            scene.member_adds_and_accepts(other)
        for person in scene.everyone:
            person.evaluate(RECORD_PLAYBACK)
            expect(person.locator(AGENT_TILE)).to_have_count(1 if other is None else 2)

    def speak(self, seconds: float, text: str = "Guten Tag", agent: OwnAccountAgent | None = None) -> None:
        """Say a sentence of an agent that is silent and return once it started; behind a playing sentence the wait
        would hold the page routes, so `speak_behind` serves that."""
        self.speak_behind(seconds, text, agent).result(REQUEST_SECONDS)

    def speak_behind(
        self, seconds: float, text: str = "Guten Tag", agent: OwnAccountAgent | None = None
    ) -> Future[dict[str, Any]]:
        """Say a sentence and return at once with its answer to come when it starts, behind the ones before it."""
        speaking(self.speech_origin, chunked_wav(silent_wav(seconds)))
        place = PlaceRef(kind=CALL_KIND, id=self.room)
        return (self.scene.agent if agent is None else agent).call_unawaited(
            SPEAK_TOOL, {"place": place.model_dump(), "text": text}
        )

    def leave(self) -> None:
        self.scene.agent.call(LEAVE_CALL, {"room": self.room})

    def started(self, person: Page) -> list[float]:
        return [round(seconds, 2) for seconds in person.evaluate("() => window.soundsStarted")]


@pytest.fixture
def speaker(scene_of: Callable[[dict[str, Any]], Scene], speech_origin: Origin, form_factor: dict[str, Any]) -> Speaker:
    return Speaker(scene_of(form_factor), speech_origin)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_an_agents_sentences_sound_once_each_in_order_in_every_browser_and_its_tile_shows_speaking(
    speaker: Speaker,
) -> None:
    """CALL-60: A joined agent's `speak` sounds in every browser of the call, in order behind its earlier sentences,
    and the agent's tile shows speaking while its sound plays and no longer when it ended."""
    everyone = speaker.scene.everyone

    speaker.speak(SOUND_SECONDS)

    for person in everyone:
        expect(person.locator(AGENT_TILE)).to_have_class(SPEAKING_TILE)
    for person in everyone:
        expect(person.locator(AGENT_TILE)).not_to_have_class(SPEAKING_TILE)
        assert speaker.started(person) == [SOUND_SECONDS]

    after_silence, behind_it = LATER_SOUND_SECONDS
    speaker.speak(after_silence)
    behind_answer = speaker.speak_behind(behind_it)

    for person in everyone:
        person.wait_for_function("() => window.soundsStarted.length === 3", timeout=PLAYBACK_WAIT_MILLISECONDS)
        assert speaker.started(person) == [SOUND_SECONDS, *LATER_SOUND_SECONDS]
        expect(person.locator(AGENT_TILE)).not_to_have_class(SPEAKING_TILE)
    assert behind_answer.result(REQUEST_SECONDS) == {"duration_ms": round(behind_it * MILLISECONDS_PER_SECOND)}


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_an_agent_that_leaves_takes_its_tile_and_the_sound_it_plays_from_every_browser(speaker: Speaker) -> None:
    """CALL-60: The agent's leave closes its player in every browser: the sound that plays ends and its tile is gone,
    with no error shown."""
    everyone = speaker.scene.everyone
    speaker.speak(LATER_SOUND_SECONDS[0])
    for person in everyone:
        expect(person.locator(AGENT_TILE)).to_have_class(SPEAKING_TILE)

    speaker.leave()

    for person in everyone:
        expect(person.locator(AGENT_TILE)).to_have_count(0)
        assert person.evaluate("() => window.soundsStopped") == 1


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_both_agents_of_one_seat_speak_each_through_its_own_player_and_its_own_tile(
    scene_of: Callable[[dict[str, Any]], Scene],
    speech_origin: Origin,
    second_own_account_agent: OwnAccountAgent,
    form_factor: dict[str, Any],
) -> None:
    """CALL-60: Two agents of one seat speak at once: each sound plays in its own player in every browser, and each
    tile shows speaking apart from the other's. The players start together, so the order of their starts is open."""
    speaker = Speaker(scene_of(form_factor), speech_origin, other=second_own_account_agent)
    first, second = speaker.scene.agent, second_own_account_agent

    speaker.speak(LONGER_SOUND_SECONDS, agent=first)
    speaker.speak(SOUND_SECONDS, agent=second)

    everyone = speaker.scene.everyone
    tiles_of = {
        person: [person.locator(AGENT_TILE).filter(has_text=agent.name) for agent in (first, second)]
        for person in everyone
    }
    for tiles in tiles_of.values():
        expect(tiles[0]).to_have_class(SPEAKING_TILE)
        expect(tiles[1]).to_have_class(SPEAKING_TILE)
    for tiles in tiles_of.values():
        expect(tiles[1]).not_to_have_class(SPEAKING_TILE)
        expect(tiles[0]).to_have_class(SPEAKING_TILE)
    for person, tiles in tiles_of.items():
        expect(tiles[0]).not_to_have_class(SPEAKING_TILE)
        assert sorted(speaker.started(person)) == [SOUND_SECONDS, LONGER_SOUND_SECONDS]
