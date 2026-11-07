"""The quiz master's spoken lines in two real browsers, over a fake speech origin and a scripted agent backend."""

from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect
from speech_runtime_client.wire import SPEECH_PATH, RefusalCode

from hopin.agent.feature import GameFeature
from hopin.agent.settings import SpeechSettings
from hopin.game.vocabulary import VOICE_PATH
from tests.e2e.game.game_page import (
    CARD,
    GAME_MASTER_LINES,
    MUTE_VOICE,
    answer_on_card,
    open_chat,
    start_quiz,
)
from tests.e2e.support.calls import join, open_host, open_more
from tests.e2e.support.media import RECORD_PEER_CONNECTIONS, wait_for_peer_count
from tests.e2e.support.people import FORM_FACTORS
from tests.e2e.support.server import RunningServer, loopback_sign_in, running_hopin
from tests.game.support.fake_agent_backend import FakeAgentBackend, Succeeds
from tests.game.support.speech_origin import AUDIENCE_SCOPE, chunked_wav, refusal_answer, silent_wav, speaking
from tests.support.loopback_origin import ARRIVAL_CAP_SECONDS, Origin

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

pytestmark = pytest.mark.e2e

QUESTION = "Welcher Fluss fließt durch Lissabon?"
REVEAL = "Der Tejo."
# A page records each blob audio it is asked to play and whether the browser allowed it.
RECORD_VOICES = """
window.voicesPlayed = [];
const play = HTMLMediaElement.prototype.play;
HTMLMediaElement.prototype.play = function () {
    const started = play.call(this);
    if (this.src.startsWith("blob:")) {
        started.then(() => window.voicesPlayed.push("started"), () => window.voicesPlayed.push("refused"));
    }
    return started;
};
"""


@pytest.fixture(scope="module")
def speech_origin() -> Iterator[Origin]:
    origin = Origin()
    yield origin
    origin.close()


@pytest.fixture(scope="module")
def speech_server(
    speech_origin: Origin, agent_backend: FakeAgentBackend, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[RunningServer]:
    """HopIn with the game wired in and its voice on, the agent backend and the speech service fakes."""
    speech = SpeechSettings(origin=speech_origin.base_url, voice="piper", audience_scope=AUDIENCE_SCOPE)
    sign_in = loopback_sign_in(tmp_path_factory.mktemp("voice-sign-in"))
    with running_hopin(sign_in, tmp_path_factory.mktemp("voice-tls"), GameFeature(agent_backend, speech)) as server:
        yield server


@pytest.fixture
def e2e_server(speech_server: RunningServer) -> RunningServer:
    return speech_server


def open_call_recording_voices(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> tuple[Page, Page]:
    host, invite_link = open_host(open_person, base_url, page_errors, form_factor, RECORD_VOICES)
    guest = join(open_person, invite_link, form_factor, (RECORD_PEER_CONNECTIONS, RECORD_VOICES), page_errors)
    wait_for_peer_count(host, 1)
    wait_for_peer_count(guest, 1)
    return host, guest


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_both_browsers_start_the_voice_of_the_question(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    speech_origin: Origin,
    agent_backend: FakeAgentBackend,
    page_errors: list[str],
    form_factor: dict[str, Any],
) -> None:
    """GM-32: Both browsers start the voice of the question."""
    speaking(speech_origin, chunked_wav(silent_wav()))
    agent_backend.answer_next(Succeeds(QUESTION))
    host, guest = open_call_recording_voices(open_person, e2e_server.base_url, page_errors, form_factor)

    start_quiz(host)

    for person in (host, guest):
        person.wait_for_function("() => window.voicesPlayed.length > 0")
        assert person.evaluate("() => window.voicesPlayed") == ["started"]


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_service_that_refuses_leaves_the_quiz_as_it_runs_without_a_voice(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    speech_origin: Origin,
    agent_backend: FakeAgentBackend,
    page_errors: list[str],
    form_factor: dict[str, Any],
) -> None:
    """GM-34: A service that refuses leaves the quiz as it runs without a voice."""
    speaking(speech_origin, refusal_answer(RefusalCode.BUSY, retry_after_seconds=1))
    agent_backend.answer_next(Succeeds(QUESTION), Succeeds(REVEAL))
    host, guest = open_call_recording_voices(open_person, e2e_server.base_url, page_errors, form_factor)

    start_quiz(host)

    for person in (host, guest):
        open_chat(person)
        expect(person.locator(CARD)).to_contain_text(QUESTION)
    assert speech_origin.received[SPEECH_PATH].wait(ARRIVAL_CAP_SECONDS)
    # The reveal's run starts only after the question's voice was sent or failed, so its line follows the outcome.
    for person in (host, guest):
        answer_on_card(person, "Tejo")
    for person in (host, guest):
        expect(person.locator(GAME_MASTER_LINES).filter(has_text=REVEAL)).to_have_count(1)
    for person in (host, guest):
        assert person.evaluate("() => window.voicesPlayed") == []


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_muted_browser_fetches_no_voice_while_the_other_still_hears_it(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    speech_origin: Origin,
    agent_backend: FakeAgentBackend,
    page_errors: list[str],
    form_factor: dict[str, Any],
) -> None:
    """GM-37: A muted browser fetches no voice while the other still hears it, and the text lines are the same."""
    speaking(speech_origin, chunked_wav(silent_wav()))
    agent_backend.answer_next(Succeeds(QUESTION), Succeeds(REVEAL))
    host, guest = open_call_recording_voices(open_person, e2e_server.base_url, page_errors, form_factor)
    voice_fetches: dict[Page, list[str]] = {host: [], guest: []}
    for person, fetches in voice_fetches.items():
        person.on("request", lambda request, fetches=fetches: fetches.append(request.url))
    start_quiz(host)
    for person in (host, guest):
        person.wait_for_function("() => window.voicesPlayed.length > 0")

    open_more(guest)
    mute = guest.locator(MUTE_VOICE)
    expect(mute).to_have_attribute("aria-pressed", "true")
    mute.click()
    expect(mute).to_have_attribute("aria-pressed", "false")
    for person in (host, guest):
        open_chat(person)
        answer_on_card(person, "Tejo")

    for person in (host, guest):
        expect(person.locator(GAME_MASTER_LINES).filter(has_text=REVEAL)).to_have_count(1)
    host.wait_for_function("() => window.voicesPlayed.length > 1")
    fetched = {person: [url for url in urls if VOICE_PATH in url] for person, urls in voice_fetches.items()}
    assert len(fetched[host]) > 1
    assert len(fetched[guest]) == 1
    assert guest.evaluate("() => window.voicesPlayed") == ["started"]
