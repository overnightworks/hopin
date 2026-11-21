import time
from contextlib import ExitStack
from functools import partial
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect
from speech_client.wire import VoiceId

from hopin.participation.vocabulary import VOICE_PATH, VOICE_RECOVERY_SECONDS, AgentRefusal
from tests.e2e.support.dialed_agent import AGENT_TILE, REQUEST_SECONDS
from tests.e2e.support.people import FORM_FACTORS
from tests.e2e.support.speaker import RECORD_PLAYBACK, SPEAKING_TILE, Speaker, speaking_hopin
from tests.support.loopback_origin import Origin
from tests.support.speech_origin import (
    offering,
    speech_requests,
    spoken_request,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from tests.e2e.support.dialed_agent import OwnAccountAgent
    from tests.e2e.support.participation_scene import Scene
    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

AGENT_LINE = "#chatMessages .chat-msg:has(.participation-agent-sender)"
ABORTED_VOICE_FAILURE = "net::ERR_INCOMPLETE_CHUNKED_ENCODING"
ABORTED_VOICE_CONSOLE_LINE = f"Failed to load resource: {ABORTED_VOICE_FAILURE}"
SOUND_SECONDS = 1.0
LONGER_SOUND_SECONDS = 5.0
POLL_SECONDS = 0.2
LATER_SOUND_SECONDS = (1.5, 1.25)
PLAYBACK_WAIT_MILLISECONDS = 15_000


@pytest.fixture(scope="module")
def speech_origin() -> Iterator[Origin]:
    origin = Origin()
    offering(origin, *VoiceId)
    yield origin
    origin.close()


@pytest.fixture(scope="module")
def voicing_server(tmp_path_factory: pytest.TempPathFactory, speech_origin: Origin) -> Iterator[RunningServer]:
    with speaking_hopin(tmp_path_factory, "voice", speech_origin.base_url) as server:
        yield server


@pytest.fixture
def e2e_server(voicing_server: RunningServer) -> RunningServer:
    return voicing_server


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
    assert behind_answer.result(REQUEST_SECONDS) == {"spoken": True}


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_an_agents_sentence_sounds_in_every_browser_while_speech_is_still_rendering_it(speaker: Speaker) -> None:
    """CALL-60: A sentence starts when its first chunk arrives: `speak` answers and every browser plays and shows the
    tile speaking while speech has rendered only the first half, and the sound goes on when the rest
    arrives, also after the first half has played out, until the tile shows present again."""
    everyone = speaker.scene.everyone

    answered = speaker.speak_while_speech_pauses(SOUND_SECONDS)

    assert answered.result(REQUEST_SECONDS) == {"spoken": True}
    for person in everyone:
        expect(person.locator(AGENT_TILE)).to_have_class(SPEAKING_TILE)
        person.wait_for_function("() => window.soundsStarted.length === 1", timeout=PLAYBACK_WAIT_MILLISECONDS)
    for person in everyone:
        person.wait_for_function(
            "() => window.sourcesStarted > 0 && window.sourcesEnded === window.sourcesStarted",
            timeout=PLAYBACK_WAIT_MILLISECONDS,
        )
        expect(person.locator(AGENT_TILE)).to_have_class(SPEAKING_TILE)
    assert not speaker.speech_has_rendered_the_rest()
    speaker.finish_rendering()
    for person in everyone:
        expect(person.locator(AGENT_TILE)).not_to_have_class(SPEAKING_TILE, timeout=PLAYBACK_WAIT_MILLISECONDS)
        assert round(sum(speaker.started(person)), 2) == SOUND_SECONDS


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_stream_that_breaks_after_its_first_chunk_cuts_the_sentence_and_logs_only_the_aborted_voice(
    speaker: Speaker, e2e_server: RunningServer, expected_request_failures: list[str], page_errors: list[str]
) -> None:
    """CALL-61: A stream that breaks after its first chunk ends the sentence that already sounds: every browser's voice
    response aborts, which is the one error its console logs, the tile shows present again, the agent's window holds
    the sentence as a cut line, the agent's next sentence plays, and a seat that loads the cut voice later gets 404
    with no page error."""
    everyone = speaker.scene.everyone
    expected_request_failures.append(f"{e2e_server.base_url}{VOICE_PATH}/")
    console_errors: list[str] = []
    aborted_voices: list[str] = []
    cut_voice_urls: dict[Page, str] = {}
    for person in everyone:
        person.on("console", lambda message: message.type == "error" and console_errors.append(message.text))
        person.on("requestfailed", lambda request: aborted_voices.append(f"{request.url}: {request.failure}"))
        person.on(
            "request",
            lambda request, seat=person: VOICE_PATH in request.url and cut_voice_urls.setdefault(seat, request.url),
        )

    answered = speaker.speak_while_speech_breaks(SOUND_SECONDS)

    assert answered.result(REQUEST_SECONDS) == {"spoken": True}
    for person in everyone:
        person.wait_for_function("() => window.soundsStarted.length === 1", timeout=PLAYBACK_WAIT_MILLISECONDS)
    with ExitStack() as aborts:
        for person in everyone:
            aborts.enter_context(person.expect_event("requestfailed"))
            aborts.enter_context(person.expect_console_message(lambda message: message.type == "error"))
        speaker.finish_rendering()

    for person in everyone:
        expect(person.locator(AGENT_TILE)).not_to_have_class(SPEAKING_TILE)
    assert (
        console_errors,
        [failure.partition(": ")[2] for failure in aborted_voices if VOICE_PATH in failure],
        [(line["text"], line["spoken"], line["cut"]) for line in speaker.window()["entries"]],
    ) == (
        [ABORTED_VOICE_CONSOLE_LINE] * len(everyone),
        [ABORTED_VOICE_FAILURE] * len(everyone),
        [("Guten Tag", True, True)],
    )

    after_the_cut, _ = LATER_SOUND_SECONDS
    speaker.speak(after_the_cut)
    for person in everyone:
        # The cut sentence's second chunk is a sound of its own when its first chunk played out before it arrived, so
        # the next sentence is the last sound started, not necessarily the second.
        person.wait_for_function(
            "seconds => Math.round(window.soundsStarted.at(-1) * 100) / 100 === seconds",
            arg=after_the_cut,
            timeout=PLAYBACK_WAIT_MILLISECONDS,
        )
    late_statuses = [
        person.evaluate("async url => (await fetch(url)).status", cut_voice_urls[person]) for person in everyone
    ]
    assert (late_statuses, page_errors) == ([HTTPStatus.NOT_FOUND] * len(everyone), [])


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_an_agent_that_leaves_takes_its_tile_and_the_sound_it_plays_from_every_browser(speaker: Speaker) -> None:
    """CALL-60: The agent's leave closes its player in every browser: the sound that plays ends and its tile is gone,
    with no error shown, and the agent holds no place there any more, which a second leave answers."""
    everyone = speaker.scene.everyone
    speaker.speak(LATER_SOUND_SECONDS[0])
    for person in everyone:
        expect(person.locator(AGENT_TILE)).to_have_class(SPEAKING_TILE)

    left = speaker.leave()

    for person in everyone:
        expect(person.locator(AGENT_TILE)).to_have_count(0)
        assert person.evaluate("() => window.soundsStopped") > 0
    assert (left, speaker.refusal_of_a_second_leave()) == ({"left": True}, AgentRefusal.GONE.value)


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


def test_the_recording_counts_the_chunks_of_one_player_as_one_sound_whatever_another_player_starts_between(
    page: Page,
) -> None:
    """The chunks that join up count as one sound even when another player starts its sound between them."""
    page.evaluate(RECORD_PLAYBACK)

    started = page.evaluate(
        """
        () => {
            const [first, second] = [new AudioContext(), new AudioContext()];
            const play = (context, seconds, when) => {
                const source = context.createBufferSource();
                source.buffer = context.createBuffer(1, Math.round(seconds * context.sampleRate), context.sampleRate);
                source.start(when);
                return when + seconds;
            };
            const joinsAt = play(first, 0.25, 0);
            play(second, 5, 0);
            play(first, 0.75, joinsAt);
            return window.soundsStarted;
        }
        """
    )

    assert sorted(started) == [SOUND_SECONDS, LONGER_SOUND_SECONDS]


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_stop_cuts_the_sentence_and_drops_the_queue_in_every_browser_and_the_agent_speaks_again(
    speaker: Speaker,
) -> None:
    """CALL-60: `stop_speaking` ends the sound that plays in every browser at once and its tile shows present again,
    the sentence waiting behind it never sounds and answers stopped, and the agent's next sentence sounds."""
    everyone = speaker.scene.everyone
    speaker.speak(LONGER_SOUND_SECONDS)
    for person in everyone:
        expect(person.locator(AGENT_TILE)).to_have_class(SPEAKING_TILE)
    behind = speaker.speak_behind(SOUND_SECONDS)
    assert speaker.speech_was_asked()

    cut = speaker.stop()

    assert cut == {"cut": "Guten Tag"}
    with pytest.raises(AssertionError, match="stopped"):
        behind.result(REQUEST_SECONDS)
    for person in everyone:
        expect(person.locator(AGENT_TILE)).not_to_have_class(SPEAKING_TILE)
        assert person.evaluate("() => window.soundsStopped") > 0

    speaker.speak(SOUND_SECONDS)

    for person in everyone:
        expect(person.locator(AGENT_TILE)).to_have_class(SPEAKING_TILE)
        person.wait_for_function("() => window.soundsStarted.length === 2", timeout=PLAYBACK_WAIT_MILLISECONDS)
        assert speaker.started(person)[1] == SOUND_SECONDS


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_sentence_whose_voice_fails_is_written_in_every_chat_and_the_window_states_the_voice(
    speaker: Speaker,
) -> None:
    """CALL-61: When the speech service cannot voice a sentence, `speak` answers written, every seat reads the sentence
    as the agent's line and hears nothing, its tile never shows speaking, the agent's window holds it as an unspoken
    line and states the voice unavailable, and the agent's next sentence that sounds states it available again."""
    everyone = speaker.scene.everyone
    for person in everyone:
        if person.locator("#moreMenu").is_visible():
            person.keyboard.press("Escape")
        person.click("#chatBtn")

    answered = speaker.speak_with_a_failing_voice("Guten Tag")

    assert answered == {"written": True}
    for person in everyone:
        expect(person.locator(AGENT_LINE)).to_have_count(1)
        expect(person.locator(AGENT_LINE)).to_contain_text("Guten Tag")
        expect(person.locator(AGENT_TILE)).not_to_have_class(SPEAKING_TILE)
        assert speaker.started(person) == []
    window = speaker.window()
    assert (window["voice_state"], [(line["text"], line["spoken"]) for line in window["entries"]]) == (
        "unavailable",
        [("Guten Tag", False)],
    )

    speaker.speak(SOUND_SECONDS)

    for person in everyone:
        person.wait_for_function("() => window.soundsStarted.length === 1", timeout=PLAYBACK_WAIT_MILLISECONDS)
    assert speaker.window()["voice_state"] == "available"


@pytest.fixture
def voices_of_the_service(speech_origin: Origin) -> Iterator[Callable[..., None]]:
    """What the speech service's health offers from now on; every voice again once the test is over."""
    yield partial(offering, speech_origin)
    offering(speech_origin, *VoiceId)


@pytest.mark.parametrize("form_factor", FORM_FACTORS[:1])
def test_an_agent_speaks_with_the_first_ready_voice_of_its_list_and_its_voice_comes_back_by_itself(
    scene_of: Callable[[dict[str, Any]], Scene],
    speech_origin: Origin,
    voices_of_the_service: Callable[..., None],
    form_factor: dict[str, Any],
) -> None:
    """CALL-60 and CALL-61: An agent whose accept lists ryan then thorsten speaks with ryan while health offers it and
    with thorsten once health offers only Piper voices, with no restart; when health offers none of its list the
    sentence is written and the window states the voice unavailable, and the window states it available again, with no
    sentence, once a voice of the list is offered."""
    speaker = Speaker(scene_of(form_factor), speech_origin, voices=[VoiceId.RYAN, VoiceId.THORSTEN])
    earlier = len(speech_requests(speech_origin))

    speaker.speak(SOUND_SECONDS)
    voices_of_the_service(VoiceId.THORSTEN, VoiceId.KERSTIN)
    speaker.speak(SOUND_SECONDS)
    voices_of_the_service(VoiceId.KERSTIN)
    written = speaker.speak_behind(SOUND_SECONDS).result(REQUEST_SECONDS)

    spoken_voices = [spoken_request(request).voice for request in speech_requests(speech_origin)[earlier:]]
    assert (spoken_voices, written, speaker.window()["voice_state"]) == (
        [VoiceId.RYAN, VoiceId.THORSTEN],
        {"written": True},
        "unavailable",
    )

    voices_of_the_service(VoiceId.KERSTIN, VoiceId.THORSTEN)

    deadline = time.monotonic() + VOICE_RECOVERY_SECONDS * 3
    while speaker.window()["voice_state"] != "available" and time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
    assert speaker.window()["voice_state"] == "available"
