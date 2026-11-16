"""The trace of a heard turn through HopIn's real `/ws` hearing route and `/mcp`, with a fake speech service for the
hearing and a fake one for the agent's sentence: the turn runs in the trace the service's transcript names, and the
agent's sentence reaches the service in it."""

import json
import secrets
from dataclasses import dataclass

import pytest
from logline.testing import configured
from speech_client.wire import TRACEPARENT_HEADER, VoiceId

from hopin.agent.speech_settings import SpeechService
from tests.support.agent_speak import speak
from tests.support.fake_speech import a_sentence
from tests.support.hearing_socket import SOUND, closed_with, hop
from tests.support.speech_origin import AUDIENCE_SCOPE, header_of, speech_requests

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class Turn:
    """What the speech service's transcript of one turn names: its trace and its item."""

    trace_id: str
    span_id: str
    item_id: str

    @property
    def traceparent(self):
        return f"00-{self.trace_id}-{self.span_id}-01"


@pytest.fixture
def turn():
    return Turn(trace_id=secrets.token_hex(16), span_id=secrets.token_hex(8), item_id=f"item_{secrets.token_hex(4)}")


@pytest.fixture
def speaking_service(speech_origin):
    return SpeechService(speech_origin.base_url, VoiceId.THORSTEN, AUDIENCE_SCOPE)


def hear_the_turn(scene, fake_speech, turn):
    """A hop in which the service hears one sentence of the turn that its transcript names the trace of."""
    with hop(scene) as socket:
        socket.send_bytes(SOUND)
        session = fake_speech.next_session()
        for event in a_sentence("Guten Tag", item_id=turn.item_id, traceparent=turn.traceparent):
            session.say(event)
        session.hang_up()
        closed_with(socket)


def test_call_58_a_heard_turn_runs_in_the_trace_the_transcript_names_and_line_entered_names_its_item(
    sponsored, fake_speech, turn, capsys
):
    with configured("hopin"):
        hear_the_turn(sponsored, fake_speech, turn)
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]

    by_event = {line["event"]: line for line in lines}
    assert (
        {by_event[event]["trace_id"] for event in ("transcript_relay.ended", "line.entered", "pulse.sent")},
        by_event["line.entered"]["item_id"],
    ) == ({turn.trace_id}, turn.item_id)


def test_call_58_the_agents_sentence_after_a_heard_turn_reaches_the_speech_service_in_the_turns_trace(
    sponsored, fake_speech, speech_origin, turn
):
    hear_the_turn(sponsored, fake_speech, turn)
    speak(sponsored)

    [request] = speech_requests(speech_origin)
    assert header_of(request, TRACEPARENT_HEADER).split("-")[1] == turn.trace_id


def test_call_58_the_hearing_session_opens_its_socket_to_the_speech_service_in_a_trace(sponsored, fake_speech, turn):
    hear_the_turn(sponsored, fake_speech, turn)

    assert fake_speech.next_session().headers.get(TRACEPARENT_HEADER) is not None
