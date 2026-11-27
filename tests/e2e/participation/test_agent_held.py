"""CALL-69 and CALL-70: a person's speech start holds the agent in real browsers, and the agent goes on or ends
silent, through a speech service that hears the sponsor's microphone and speaks the agent's sentences on one origin."""

from typing import TYPE_CHECKING, Any

import pytest
from agents.member import CONTINUE_SPEAKING_TOOL, STOP_SPEAKING_TOOL, Interrupted, Spoken
from playwright.sync_api import Page, expect
from speech_client.realtime_wire import SpeechStarted
from speech_client.wire import VoiceId

from tests.e2e.support.dialed_agent import AGENT_HEARING_SWITCH, AGENT_TILE, REQUEST_SECONDS, call_place
from tests.e2e.support.hearing_hops import open_person, settle, until  # noqa: F401 - the fixture is used by name
from tests.e2e.support.people import FORM_FACTORS
from tests.e2e.support.speaker import SPEAKING_TILE, Speaker, speaking_hopin
from tests.support.fake_speech import ITEM, FakeSpeech, completed
from tests.support.loopback_origin import Origin
from tests.support.speech_origin import offering

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from agents.member import SpeakAnswer, SpeakRefusal

    from tests.e2e.support.participation_scene import Scene
    from tests.e2e.support.server import RunningServer
    from tests.support.fake_speech import Session

pytestmark = pytest.mark.e2e

SENTENCE_SECONDS = 4.0
HELD_SENTENCE = "Guten Tag"
ANSWERING_SENTENCE = "Moment, ich höre"
PLAYBACK_WAIT_MILLISECONDS = 15_000
HOLD_ENDED = "agent_hold.ended"


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
    with speaking_hopin(tmp_path_factory, "hold", speech_service.origin, hears=True) as server:
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

    def sponsor_starts_speaking(self, item_id: str = ITEM, hold: str = "1") -> None:
        self.session.say(SpeechStarted(item_id=item_id, audio_start_ms=0))
        until(
            self.speaker.scene.owner,
            lambda: self.speaker.window().interrupted == Interrupted(id=hold),
            f"the agent's hold {hold}",
        )

    def sponsor_finishes_speaking(self) -> None:
        self.session.say(completed("Ich bin fertig"))
        until(self.speaker.scene.owner, lambda: self.speaker.window().talked_over is None, "the end of the turn")

    def speak(self, text: str, interrupted: str | None = None) -> SpeakAnswer | SpeakRefusal:
        return self.speaker.scene.agent.speak(self.speaker.room, text, interrupted)

    def call(self, tool: str, **named: Any) -> dict[str, Any]:
        place = call_place(self.speaker.room)
        return self.speaker.scene.agent.call(tool, {"place": place.model_dump(), **named})

    def sounds_started(self, person: Page) -> list[float]:
        return self.speaker.started(person)


# The pause's ramp ends when the context's clock passes its end; a frozen clock keeps every player in the ramp until
# `window.clockFrozen` is false again, with the context not yet suspended.
FREEZE_CONTEXT_CLOCKS = """
() => {
    const currentTime = Object.getOwnPropertyDescriptor(BaseAudioContext.prototype, 'currentTime').get;
    const frozenAt = new WeakMap();
    window.clockFrozen = false;
    Object.defineProperty(BaseAudioContext.prototype, 'currentTime', {
        configurable: true,
        get() {
            if (!window.clockFrozen) return currentTime.call(this);
            if (!frozenAt.has(this)) frozenAt.set(this, currentTime.call(this));
            return frozenAt.get(this);
        },
    });
}
"""


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


@pytest.fixture
def held_in_the_pausing_ramp(
    scene_of: Callable[[dict[str, Any]], Scene],
    speech_origin: Origin,
    speech_service: FakeSpeech,
    form_factor: dict[str, Any],
) -> HeldAgent:
    """The held agent, with every browser's players still in the ramp of their pause, not yet suspended."""
    held_agent = HeldAgent(Speaker(scene_of(form_factor), speech_origin), speech_service)
    for person in held_agent.everyone:
        person.wait_for_function(
            f"() => window.soundsStarted[0] > {SENTENCE_SECONDS - 0.1}", timeout=PLAYBACK_WAIT_MILLISECONDS
        )
        person.evaluate(FREEZE_CONTEXT_CLOCKS)
        person.evaluate("() => { window.clockFrozen = true; }")
    held_agent.sponsor_starts_speaking()
    for person in held_agent.everyone:
        expect(person.locator(AGENT_TILE)).not_to_have_class(SPEAKING_TILE)
        assert person.evaluate("() => window.contextsSuspended") == 0
    return held_agent


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_person_starting_to_speak_pauses_the_agents_sound_in_every_browser_and_nothing_more_sounds(
    held: HeldAgent,
) -> None:
    """CALL-69: When the sponsor starts speaking while the agent's first sentence plays and a second waits, its sound
    pauses in every browser and its tile shows present, the window shows the hold and `talked_over.playing` false in
    one read, and the second sentence never sounds and never answers without the agent's decision."""
    for person in held.everyone:
        expect(person.locator(AGENT_TILE)).not_to_have_class(SPEAKING_TILE)
        person.wait_for_function("() => window.contextsSuspended > 0", timeout=PLAYBACK_WAIT_MILLISECONDS)
        assert person.evaluate("() => window.soundsStopped") == 0
    window = held.speaker.window()

    assert window.talked_over is not None
    assert (window.interrupted, window.talked_over.playing, window.entries) == (Interrupted(id="1"), False, ())

    settle(held.speaker.scene.owner)
    held.speaker.scene.owner.wait_for_timeout(int(SENTENCE_SECONDS * 1000))
    assert [held.sounds_started(person) for person in held.everyone] == [[SENTENCE_SECONDS]] * len(held.everyone)
    assert not held.second_answer.done()


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_while_held_a_speak_with_the_hold_plays_after_the_cut_line(held: HeldAgent) -> None:
    """CALL-69: A `speak` with the hold's id drops the held rest, plays at once in every browser and enters its line
    after the cut line of the held sentence."""
    answered = held.speak(ANSWERING_SENTENCE, interrupted="1")

    for person in held.everyone:
        person.wait_for_function("() => window.soundsStarted.length === 2", timeout=PLAYBACK_WAIT_MILLISECONDS)
        assert person.evaluate("() => window.soundsStopped") > 0
    window = held.speaker.window()
    assert answered == Spoken(spoken=True)
    assert (window.interrupted is not None, [(line.text, line.cut) for line in window.entries]) == (
        False,
        [(HELD_SENTENCE, True)],
    )


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_while_held_a_stop_with_the_hold_ends_silent_and_a_later_speak_sounds(held: HeldAgent) -> None:
    """CALL-69 and CALL-70: A `stop_speaking` with the hold's id ends it silent in every browser and answers the text
    of the cut sentence, the second sentence never sounds, and a `speak` after it sounds in every browser, which a
    player left paused would not do."""
    stopped = held.call(STOP_SPEAKING_TOOL, interrupted="1")
    window = held.speaker.window()
    settle(held.speaker.scene.owner)
    silent = [held.sounds_started(person) for person in held.everyone]
    held.speak(ANSWERING_SENTENCE)

    for person in held.everyone:
        person.wait_for_function("() => window.soundsStarted.length === 2", timeout=PLAYBACK_WAIT_MILLISECONDS)
        expect(person.locator(AGENT_TILE)).to_have_class(SPEAKING_TILE)
    assert stopped == {"cut": HELD_SENTENCE}
    assert (window.interrupted is not None, silent) == (False, [[SENTENCE_SECONDS]] * len(held.everyone))


def continue_and_check_the_same_sound_goes_on(
    held: HeldAgent, after_the_continue: Callable[[Page], None] = lambda person: None
) -> None:
    continued = held.call(CONTINUE_SPEAKING_TOOL, interrupted="1")
    window = held.speaker.window()

    for person in held.everyone:
        expect(person.locator(AGENT_TILE)).to_have_class(SPEAKING_TILE)
        after_the_continue(person)
    until(
        held.speaker.scene.owner,
        lambda: [line.text for line in held.speaker.window().entries] == [HELD_SENTENCE],
        "the held sentence's line",
    )
    for person in held.everyone:
        person.wait_for_function("() => window.soundsStarted.length === 2", timeout=PLAYBACK_WAIT_MILLISECONDS)
    entries = held.speaker.window().entries
    assert continued == {"continued": True}
    assert (window.interrupted is not None, [(line.text, line.cut) for line in entries]) == (
        (False, [(HELD_SENTENCE, False)])
    )
    assert held.second_answer.result(REQUEST_SECONDS) == Spoken(spoken=True)
    assert [held.sounds_started(person) for person in held.everyone] == [[SENTENCE_SECONDS] * 2] * len(held.everyone)
    assert [person.evaluate("() => window.soundsStopped") for person in held.everyone] == [0] * len(held.everyone)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_while_held_a_continue_goes_on_with_the_same_sound_in_every_browser_and_the_queue_plays_after_it(
    held: HeldAgent,
) -> None:
    """CALL-70: A `continue_speaking` with the hold's id answers continued, the window drops `interrupted` in that
    change, the same sound goes on where it paused in every browser and plays to its end, its whole line enters
    uncut, and the second sentence plays after it."""
    for person in held.everyone:
        person.wait_for_function("() => window.contextsSuspended > 0", timeout=PLAYBACK_WAIT_MILLISECONDS)
    continue_and_check_the_same_sound_goes_on(held)
    for person in held.everyone:
        assert person.evaluate("() => window.contextsResumed") > 0


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_continue_during_the_pausing_ramp_goes_on_with_the_same_sound_and_the_queue_plays_after_it(
    held_in_the_pausing_ramp: HeldAgent,
) -> None:
    """CALL-70: A `continue_speaking` that arrives before a browser's player has suspended its context lets the same
    sound go on without a suspend, and the second sentence plays after it."""

    def unfreeze_once_no_sound_has_ended(person: Page) -> None:
        assert person.evaluate("() => window.soundsEnded") == 0
        person.evaluate("() => { window.clockFrozen = false; }")

    continue_and_check_the_same_sound_goes_on(held_in_the_pausing_ramp, unfreeze_once_no_sound_has_ended)
    for person in held_in_the_pausing_ramp.everyone:
        assert person.evaluate("() => window.contextsSuspended") == 0


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_person_who_starts_afresh_after_a_continue_holds_the_agent_again_under_a_new_id(held: HeldAgent) -> None:
    """CALL-70: After a `continue_speaking`, a person who starts speaking afresh holds the agent again, with a new id,
    and its sound pauses in every browser again."""
    held.call(CONTINUE_SPEAKING_TOOL, interrupted="1")
    for person in held.everyone:
        expect(person.locator(AGENT_TILE)).to_have_class(SPEAKING_TILE)
    held.sponsor_finishes_speaking()

    held.sponsor_starts_speaking("item_two", hold="2")

    for person in held.everyone:
        expect(person.locator(AGENT_TILE)).not_to_have_class(SPEAKING_TILE)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_every_seat_of_a_held_agent_reports_where_its_player_stood_once(
    held: HeldAgent, server_logs: list[dict[str, Any]]
) -> None:
    """CALL-69: every browser whose player paused for the hold sends one report of the sentence's voice and its played
    seconds, which the hold's line counts once for each seat when the hold ends."""
    for person in held.everyone:
        person.wait_for_function("() => window.contextsSuspended > 0", timeout=PLAYBACK_WAIT_MILLISECONDS)
    settle(held.speaker.scene.owner)

    held.call(STOP_SPEAKING_TOOL, interrupted="1")

    until(
        held.speaker.scene.owner, lambda: any(entry["event"] == HOLD_ENDED for entry in server_logs), "the hold's line"
    )
    [line] = [entry for entry in server_logs if entry["event"] == HOLD_ENDED]
    assert line["seat_report_count"] == len(held.everyone)
    assert line["seat_played_min_ms"] <= line["played_ms"]
