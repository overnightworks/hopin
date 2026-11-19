"""CALL-69: a person's speech start holds the agent in real browsers, through a speech service that hears the sponsor's
microphone and speaks the agent's sentences on one origin."""

from typing import TYPE_CHECKING, Any

import pytest
from agents.member import SPEAK_TOOL, STOP_SPEAKING_TOOL, PlaceRef
from playwright.sync_api import Page, expect
from speech_client.realtime_wire import SpeechStarted
from speech_client.wire import VoiceId

from hopin.agent.speech_settings import SpeechService
from hopin.agent.tools import CALL_KIND
from tests.e2e.participation.test_agent_speaks import AGENT_TILE, SPEAKING_TILE, Speaker
from tests.e2e.support.dialed_agent import AGENT_HEARING_SWITCH, OWNER_SUBJECT, REQUEST_SECONDS
from tests.e2e.support.hearing_hops import settle, until
from tests.e2e.support.people import FORM_FACTORS
from tests.e2e.support.server import RunningServer, loopback_sign_in, running_hopin
from tests.support.agent_adders import ADDERS_GROUP
from tests.support.fake_speech import ITEM, FakeSpeech
from tests.support.loopback_origin import Origin
from tests.support.speech_origin import AUDIENCE_SCOPE, offering

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from tests.e2e.support.participation_scene import Scene
    from tests.support.fake_speech import Session

pytestmark = pytest.mark.e2e

SENTENCE_SECONDS = 4.0
HELD_SENTENCE = "Guten Tag"
ANSWERING_SENTENCE = "Moment, ich höre"
PLAYBACK_WAIT_MILLISECONDS = 15_000


@pytest.fixture(scope="module")
def speech_origin() -> Iterator[Origin]:
    origin = Origin()
    offering(origin, *VoiceId)
    yield origin
    origin.close()


@pytest.fixture(scope="module")
def speech_service(speech_origin: Origin) -> Iterator[FakeSpeech]:
    """The speech service: it hears through its Realtime endpoint and speaks through `speech_origin`, on one origin."""
    service = FakeSpeech(speech_origin.port)
    service.start()
    yield service
    service.stop()


@pytest.fixture(scope="module")
def holding_server(tmp_path_factory: pytest.TempPathFactory, speech_service: FakeSpeech) -> Iterator[RunningServer]:
    """HopIn with participation, hearing and speaking on, through `speech_service`."""
    loopback = loopback_sign_in(tmp_path_factory.mktemp("hold-sign-in"))
    with running_hopin(
        loopback,
        tmp_path_factory.mktemp("hold-tls"),
        participation=True,
        hears=True,
        speaks=True,
        speech=SpeechService(speech_service.origin, VoiceId.THORSTEN, AUDIENCE_SCOPE),
        agent_adders={OWNER_SUBJECT: ADDERS_GROUP},
    ) as server:
        yield server


@pytest.fixture
def e2e_server(holding_server: RunningServer) -> RunningServer:
    return holding_server


class HeldAgent:
    """The call of three people whose agent plays a first of two sentences and waits to play the second, while its
    sponsor's hop to the speech service is open and the sponsor starts speaking."""

    def __init__(self, speaker: Speaker, speech_service: FakeSpeech) -> None:
        self.speaker = speaker
        self.everyone = speaker.scene.everyone
        sessions_before = len(speech_service.sessions)
        speaker.scene.owner.locator(AGENT_HEARING_SWITCH).click()
        self.session: Session = speech_service.next_session(sessions_before + 1)
        self.session.wait_for_audio(1)
        speaker.speak(SENTENCE_SECONDS, HELD_SENTENCE)
        self.second_answer = speaker.speak_behind(SENTENCE_SECONDS, "Zweiter Satz")
        for person in self.everyone:
            expect(person.locator(AGENT_TILE)).to_have_class(SPEAKING_TILE)

    def sponsor_starts_speaking(self) -> None:
        self.session.say(SpeechStarted(item_id=ITEM, audio_start_ms=0))
        until(self.speaker.scene.owner, lambda: "interrupted" in self.speaker.window(), "the agent's hold")

    def call(self, tool: str, **named: Any) -> dict[str, Any]:
        place = PlaceRef(kind=CALL_KIND, id=self.speaker.room)
        return self.speaker.scene.agent.call(tool, {"place": place.model_dump(), **named})

    def refusal_of(self, tool: str, **named: Any) -> str:
        """The refusal a call of the tool is answered with."""
        with pytest.raises(AssertionError) as refused:
            self.call(tool, **named)
        return str(refused.value)

    def waiting_speak_refusal(self) -> str:
        """The refusal the `speak` that waited behind the held sentence is answered with."""
        with pytest.raises(AssertionError) as refused:
            self.second_answer.result(REQUEST_SECONDS)
        return str(refused.value)

    def sounds_started(self, person: Page) -> list[float]:
        return self.speaker.started(person)


@pytest.fixture
def held(
    scene_of: Callable[[dict[str, Any]], Scene],
    speech_origin: Origin,
    speech_service: FakeSpeech,
    form_factor: dict[str, Any],
) -> HeldAgent:
    held_agent = HeldAgent(Speaker(scene_of(form_factor), speech_origin), speech_service)
    held_agent.sponsor_starts_speaking()
    return held_agent


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_person_starting_to_speak_ends_the_agents_sound_in_every_browser_and_nothing_more_sounds(
    held: HeldAgent,
) -> None:
    """CALL-69: When the sponsor starts speaking while the agent's first sentence plays and a second waits, its sound
    ends in every browser and its tile shows present, the window shows the hold and `talked_over.playing` false in one
    read, and the second sentence never sounds and never answers without the agent's decision."""
    for person in held.everyone:
        expect(person.locator(AGENT_TILE)).not_to_have_class(SPEAKING_TILE)
        assert person.evaluate("() => window.soundsStopped") > 0
    window = held.speaker.window()

    assert (window["interrupted"], window["talked_over"]["playing"], window["entries"]) == ({"id": "1"}, False, [])

    settle(held.speaker.scene.owner)
    held.speaker.scene.owner.wait_for_timeout(int(SENTENCE_SECONDS * 1000))
    assert [held.sounds_started(person) for person in held.everyone] == [[SENTENCE_SECONDS]] * len(held.everyone)
    assert not held.second_answer.done()


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_while_held_a_speak_without_the_hold_is_refused_and_one_with_it_plays_after_the_cut_line(
    held: HeldAgent,
) -> None:
    """CALL-69: A `speak` without the hold's id is refused `held` and changes nothing; one with it drops the held
    rest, answers the waiting `speak` as stopped, plays at once in every browser and enters its line after the cut
    line of the held sentence."""
    refused_without_the_hold = held.refusal_of(SPEAK_TOOL, text=ANSWERING_SENTENCE)

    answered = held.call(SPEAK_TOOL, text=ANSWERING_SENTENCE, interrupted="1")

    for person in held.everyone:
        person.wait_for_function("() => window.soundsStarted.length === 2", timeout=PLAYBACK_WAIT_MILLISECONDS)
    window = held.speaker.window()
    assert ('"refused":"held"' in refused_without_the_hold, answered) == (True, {"spoken": True})
    assert '"refused":"stopped"' in held.waiting_speak_refusal()
    assert ("interrupted" in window, [(line["text"], line["cut"]) for line in window["entries"]]) == (
        False,
        [(HELD_SENTENCE, True)],
    )


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_while_held_a_stop_with_a_stale_id_is_refused_and_one_with_the_hold_answers_the_cut_sentence(
    held: HeldAgent,
) -> None:
    """CALL-69: A `stop_speaking` with an id that is not the hold's is refused `not_interrupted` and the hold stands;
    one with the hold's id ends it silent and answers the text of the cut sentence, and nothing more sounds."""
    stale = held.refusal_of(STOP_SPEAKING_TOOL, interrupted="9")
    standing = held.speaker.window()["interrupted"]

    stopped = held.call(STOP_SPEAKING_TOOL, interrupted="1")

    window = held.speaker.window()
    settle(held.speaker.scene.owner)
    assert ('"refused":"not_interrupted"' in stale, standing, stopped) == (True, {"id": "1"}, {"cut": HELD_SENTENCE})
    assert ("interrupted" in window, [held.sounds_started(person) for person in held.everyone]) == (
        False,
        [[SENTENCE_SECONDS]] * len(held.everyone),
    )
