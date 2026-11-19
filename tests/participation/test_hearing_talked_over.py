"""CALL-54 and CALL-58: a person talking over an agent's sentence through HopIn's real `/ws` hearing route, with fake
speech services on loopback ports, and the talked-over record read through the real `/mcp`."""

import time

import pytest
from agents.member import STOP_SPEAKING_TOOL
from speech_client.realtime_wire import SpeechStarted, TranscriptDelta
from speech_client.wire import VoiceId

from hopin.agent.speech_settings import SpeechService
from hopin.agent.tools import CALL_KIND, participation_tools
from hopin.participation.participants import Caller
from hopin.participation.vocabulary import ParticipationSignal, SpeakStop
from tests.participation.support.speaking_scene import refusal_of
from tests.participation.support.tool_calls import call_in
from tests.support.agent_speak import SENTENCE_SECONDS, TEXT, speak
from tests.support.fake_speech import ITEM, WAIT_SECONDS, FakeSpeech, completed
from tests.support.hearing_socket import SOUND, closed_with, hop
from tests.support.loopback_origin import Origin
from tests.support.speech_origin import AUDIENCE_SCOPE, chunked_wav, offering, silent_wav, speaking

pytestmark = pytest.mark.integration

POLL_SECONDS = 0.005


@pytest.fixture
def fake_speech():
    service = FakeSpeech()
    service.start()
    yield service
    service.stop()


@pytest.fixture
def speech_origin():
    origin = Origin()
    offering(origin, *VoiceId)
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


def window_when(scene, condition):
    """The agent's window once `condition` holds of it; the server handles the service's events on its own thread."""
    deadline = time.monotonic() + WAIT_SECONDS
    window = scene.window()
    while not condition(window):
        assert time.monotonic() < deadline, f"the window never came to hold what was awaited: {window}"
        time.sleep(POLL_SECONDS)
        window = scene.window()
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
            {"id": "1", "playing": False, "speech": [{"name": name, "text": ""}]},
            {"id": "1", "playing": False, "speech": [{"name": name, "text": "Moment"}]},
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

        assert "talked_over" not in agent_speaking.window()

    def test_call_69_the_agent_is_held_in_the_change_that_opens_the_record_and_its_sentence_never_ends_by_itself(
        self, agent_speaking, fake_speech
    ):
        with hop(agent_speaking) as socket:
            socket.send_bytes(SOUND)
            session = fake_speech.next_session()
            session.say(SpeechStarted(item_id=ITEM, audio_start_ms=0))
            started = window_when(agent_speaking, lambda window: "talked_over" in window)

            agent_speaking.call.pass_time(SENTENCE_SECONDS)
            window = agent_speaking.window()
            session.hang_up()
            closed_with(socket)

        assert (started["interrupted"], window["interrupted"], window["entries"]) == ({"id": "1"}, {"id": "1"}, [])


@pytest.fixture
def held_agent(agent_speaking, fake_speech):
    """The scene whose agent's sentence plays and who is held, the owner's hop gone."""
    with hop(agent_speaking) as socket:
        socket.send_bytes(SOUND)
        session = fake_speech.next_session()
        session.say(SpeechStarted(item_id=ITEM, audio_start_ms=0))
        window_when(agent_speaking, lambda window: "interrupted" in window)
        session.hang_up()
        closed_with(socket)
    return agent_speaking


class TestTheAgentDecidesWhileHeld:
    def test_call_69_a_speak_without_the_hold_is_refused_held(self, held_agent):
        assert refusal_of(speak(held_agent, seconds=None)) == (True, {"refused": "held"})

    def test_call_69_a_speak_with_a_stale_hold_is_refused_not_interrupted(self, held_agent):
        assert refusal_of(speak(held_agent, seconds=None, interrupted="9")) == (True, {"refused": "not_interrupted"})

    def test_call_69_a_stop_with_a_stale_hold_is_refused_not_interrupted_and_the_hold_stands(self, held_agent):
        refused = call_in(held_agent, tool=STOP_SPEAKING_TOOL, interrupted="9")

        assert (refusal_of(refused), held_agent.window()["interrupted"]) == (
            (True, {"refused": "not_interrupted"}),
            {"id": "1"},
        )

    def test_call_69_a_stop_with_the_hold_answers_the_cut_text_and_the_window_leaves_the_hold_with_the_cut_line(
        self, held_agent
    ):
        stopped = call_in(held_agent, tool=STOP_SPEAKING_TOOL, interrupted="1")

        window = held_agent.window()
        assert (
            (stopped.is_error, stopped.outcome),
            "interrupted" in window,
            [(e["text"], e["cut"]) for e in window["entries"]],
        ) == (
            (False, {"cut": TEXT}),
            False,
            [(TEXT, True)],
        )

    def test_call_69_a_speak_with_the_hold_plays_after_the_cut_line(self, held_agent):
        spoken = speak(held_agent, "Zweiter Satz", interrupted="1")

        window = held_agent.window()
        assert (
            (spoken.is_error, spoken.outcome),
            "interrupted" in window,
            [(e["text"], e["cut"]) for e in window["entries"]],
        ) == (
            (False, {"spoken": True}),
            False,
            [(TEXT, True)],
        )


class TestAStopThatCutNothingThatHadStarted:
    @pytest.mark.asyncio
    async def test_call_69_it_answers_dropped(self):
        class HeldWithNothingStarted:
            async def stop_speaking(self, person, room_id, interrupted=None):
                return SpeakStop.DROPPED

        [stop] = [
            tool
            for tool in participation_tools(HeldWithNothingStarted, None)
            if tool.declaration.name == STOP_SPEAKING_TOOL
        ]

        answered = await stop.handler(Caller(None), {"place": {"kind": CALL_KIND, "id": "room"}})

        assert (answered.is_error, answered.content) == (False, '{"dropped":true}')
