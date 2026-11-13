"""CALL-54 and CALL-58: a person talking over an agent's sentence through HopIn's real `/ws` hearing route, with fake
speech services on loopback ports, and the talked-over record read through the real `/mcp`."""

import json
import time

import pytest
from speech_client.wire import SpeechStarted, TranscriptDelta, VoiceId

from hopin.agent.speech_settings import SpeechService
from hopin.participation.vocabulary import ParticipationSignal
from tests.participation.test_hearing_route import SOUND, closed_with, hop
from tests.participation.test_speaking_tool import SENTENCE_SECONDS, TEXT, speak
from tests.support.fake_speech import ITEM, WAIT_SECONDS, FakeSpeech, completed
from tests.support.loopback_origin import Origin
from tests.support.speech_origin import AUDIENCE_SCOPE, chunked_wav, silent_wav, speaking

pytestmark = pytest.mark.integration

POLL_SECONDS = 0.005


@pytest.fixture
def fake_speech():
    service = FakeSpeech()
    service.start()
    yield service
    service.stop()


@pytest.fixture
def hearing_service(fake_speech):
    return SpeechService(fake_speech.origin, VoiceId.THORSTEN, AUDIENCE_SCOPE)


@pytest.fixture
def speech_origin():
    origin = Origin()
    speaking(origin, chunked_wav(silent_wav(SENTENCE_SECONDS)))
    yield origin
    origin.close()


@pytest.fixture
def speaking_service(speech_origin):
    return SpeechService(speech_origin.base_url, VoiceId.THORSTEN, AUDIENCE_SCOPE)


@pytest.fixture
def agent_speaking(joined_agent):
    """The scene with its owner's agent joined, hearing the owner, and one of its sentences playing."""
    joined_agent.owner.frame_of_type(ParticipationSignal.AGENT_HEARING.value)
    speak(joined_agent)
    return joined_agent


def window_of(scene):
    answer = scene.call.window(scene.token, scene.owner.room_id)
    return json.loads(answer["result"]["contents"][0]["text"])


def window_when(scene, condition):
    """The agent's window once `condition` holds of it; the server handles the service's events on its own thread."""
    deadline = time.monotonic() + WAIT_SECONDS
    window = window_of(scene)
    while not condition(window):
        assert time.monotonic() < deadline, f"the window never came to hold what was awaited: {window}"
        time.sleep(POLL_SECONDS)
        window = window_of(scene)
    return window


def partial_of(scene, text):
    return lambda window: "talked_over" in window and window["talked_over"]["speech"][0]["text"] == text


class TestAPersonTalksOverTheAgentThroughTheRoute:
    def test_call_54_the_person_starting_to_speak_and_the_words_so_far_stand_in_the_agents_window(
        self, agent_speaking, fake_speech
    ):
        with hop(agent_speaking) as socket:
            socket.send_bytes(SOUND)
            session = fake_speech.next_session()
            session.say(SpeechStarted(item_id=ITEM, audio_start_ms=0))
            started = window_when(agent_speaking, lambda window: "talked_over" in window)
            session.say(TranscriptDelta(item_id=ITEM, delta="Moment"))
            heard = window_when(agent_speaking, partial_of(agent_speaking, "Moment"))
            session.hang_up()
            closed_with(socket)

        name = agent_speaking.owner_account.username
        assert (started["talked_over"], heard["talked_over"]) == (
            {"id": "1", "playing": True, "speech": [{"name": name, "text": ""}]},
            {"id": "1", "playing": True, "speech": [{"name": name, "text": "Moment"}]},
        )

    def test_call_54_the_finished_line_replaces_the_partial_and_the_record_is_gone_with_the_turn(
        self, agent_speaking, fake_speech
    ):
        with hop(agent_speaking) as socket:
            socket.send_bytes(SOUND)
            session = fake_speech.next_session()
            session.say(SpeechStarted(item_id=ITEM, audio_start_ms=0))
            session.say(TranscriptDelta(item_id=ITEM, delta="Moment"))
            window_when(agent_speaking, partial_of(agent_speaking, "Moment"))
            session.say(completed("Moment bitte"))
            window = window_when(agent_speaking, lambda read: read["entries"])
            session.hang_up()
            closed_with(socket)

        assert ("talked_over" in window, [entry["text"] for entry in window["entries"]]) == (False, ["Moment bitte"])

    def test_call_54_a_turn_without_words_removes_the_record(self, agent_speaking, fake_speech):
        with hop(agent_speaking) as socket:
            socket.send_bytes(SOUND)
            session = fake_speech.next_session()
            session.say(SpeechStarted(item_id=ITEM, audio_start_ms=0))
            window_when(agent_speaking, lambda window: "talked_over" in window)
            session.say(completed(""))
            window = window_when(agent_speaking, lambda read: "talked_over" not in read)
            session.hang_up()
            closed_with(socket)

        assert window["entries"] == []

    def test_call_54_the_end_of_the_hop_removes_the_record_of_a_turn_that_never_ended(
        self, agent_speaking, fake_speech
    ):
        with hop(agent_speaking) as socket:
            socket.send_bytes(SOUND)
            session = fake_speech.next_session()
            session.say(SpeechStarted(item_id=ITEM, audio_start_ms=0))
            window_when(agent_speaking, lambda window: "talked_over" in window)
            session.hang_up()
            closed_with(socket)

        assert "talked_over" not in window_of(agent_speaking)

    def test_call_54_the_record_says_the_sentence_plays_no_more_once_it_ended(self, agent_speaking, fake_speech):
        with hop(agent_speaking) as socket:
            socket.send_bytes(SOUND)
            session = fake_speech.next_session()
            session.say(SpeechStarted(item_id=ITEM, audio_start_ms=0))
            window_when(agent_speaking, lambda window: "talked_over" in window)

            agent_speaking.call.pass_time(SENTENCE_SECONDS)
            window = window_of(agent_speaking)
            session.hang_up()
            closed_with(socket)

        assert (window["talked_over"]["playing"], [entry["text"] for entry in window["entries"]]) == (False, [TEXT])
