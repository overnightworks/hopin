"""The trace of a heard turn through HopIn's real `/ws` hearing route and `/mcp`, with a fake speech service for the
hearing and a fake one for the agent's sentence: the turn runs in the trace the service's transcript names, and the
agent's sentence reaches the service in it."""

import json

import pytest
from logline.testing import configured
from speech_client.wire import TRACEPARENT_HEADER, VoiceId

from hopin.agent.speech_settings import SpeechService
from tests.support.agent_speak import speak
from tests.support.fake_speech import a_sentence
from tests.support.hearing_socket import SOUND, closed_with, hop
from tests.support.speech_origin import AUDIENCE_SCOPE, header_of, speech_requests

pytestmark = pytest.mark.integration

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TURN_TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"
ITEM_ID = "item_of_the_turn"


@pytest.fixture
def speaking_service(speech_origin):
    return SpeechService(speech_origin.base_url, VoiceId.THORSTEN, AUDIENCE_SCOPE)


def hear_the_turn(scene, fake_speech):
    """A hop in which the service hears one sentence of the turn that its transcript names the trace of."""
    with hop(scene) as socket:
        socket.send_bytes(SOUND)
        session = fake_speech.next_session()
        for event in a_sentence("Guten Tag", item_id=ITEM_ID, traceparent=TURN_TRACEPARENT):
            session.say(event)
        session.hang_up()
        closed_with(socket)


def test_call_58_a_heard_turn_runs_in_the_trace_the_transcript_names_and_line_entered_names_its_item(
    sponsored, fake_speech, capsys
):
    with configured("hopin"):
        hear_the_turn(sponsored, fake_speech)
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]

    by_event = {line["event"]: line for line in lines}
    assert (
        {by_event[event]["trace_id"] for event in ("transcript_relay.ended", "line.entered", "pulse.sent")},
        by_event["line.entered"]["item_id"],
    ) == ({TRACE_ID}, ITEM_ID)


def test_call_58_the_agents_sentence_after_a_heard_turn_reaches_the_speech_service_in_the_turns_trace(
    sponsored, fake_speech, speech_origin
):
    hear_the_turn(sponsored, fake_speech)
    speak(sponsored)

    [request] = speech_requests(speech_origin)
    assert header_of(request, TRACEPARENT_HEADER).split("-")[1] == TRACE_ID


def test_call_58_the_hearing_session_opens_its_socket_to_the_speech_service_in_a_trace(sponsored, fake_speech):
    hear_the_turn(sponsored, fake_speech)

    assert fake_speech.next_session().headers.get(TRACEPARENT_HEADER) is not None
