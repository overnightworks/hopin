"""The trace of a heard turn and of an agent's speak through HopIn's real `/ws` hearing route and `/mcp`, with a fake
speech service for the hearing and a fake one for the agent's sentence: the turn runs in the trace the service's
transcript names, a line enters the agent's place with the trace it entered in, and a speak runs in the trace its
request carries."""

import json
import logging
import secrets
import threading
from dataclasses import dataclass

import pytest
from logline import unit_trace
from logline.testing import configured
from speech_client.wire import TRACEPARENT_HEADER, VoiceId

from hopin.agent.speech_settings import SpeechService
from hopin.agent.tools import JOIN_CALL, SAY
from tests.support.agent_speak import SENTENCE_SECONDS, speak
from tests.support.fake_speech import a_sentence
from tests.support.hearing_socket import SOUND, closed_with, hop
from tests.support.speech_origin import AUDIENCE_SCOPE, header_of, speech_requests

pytestmark = pytest.mark.integration


@pytest.fixture
def hearing_service(fake_speech_hearing):
    return fake_speech_hearing


DROP_HANDLED_SECONDS = 10
SOCKET_ENDED = "websocket.ended"


class SocketEnd(logging.Handler):
    """Sets `happened` when a record says that a socket ended, so a test waits on the end and not on a clock."""

    def __init__(self):
        super().__init__()
        self.happened = threading.Event()

    def emit(self, record):
        if SOCKET_ENDED in str(record.msg):
            self.happened.set()


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


def test_a_heard_turn_runs_in_the_trace_the_transcript_names_and_line_entered_names_its_item(
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


def logged(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]


def trace_id_of(traceparent):
    return traceparent.split("-")[1]


def a_traceparent():
    return f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"


def test_a_heard_line_enters_the_agents_place_with_the_trace_of_its_turn(sponsored, fake_speech, turn):
    hear_the_turn(sponsored, fake_speech, turn)

    [entry] = sponsored.entries()
    assert trace_id_of(entry["traceparent"]) == turn.trace_id


def test_a_line_the_agent_writes_enters_its_place_with_the_trace_of_its_request(joined_agent):
    request_trace = a_traceparent()

    joined_agent.call.call(
        joined_agent.token, SAY, {"room": joined_agent.owner.room_id, "text": "hello"}, traceparent=request_trace
    )

    [entry] = joined_agent.entries()
    assert trace_id_of(entry["traceparent"]) == trace_id_of(request_trace)


@pytest.mark.parametrize("close_code", [1000, 1006])
def test_a_sentence_cut_by_a_leaving_person_reaches_the_other_agent_outside_the_leavers_session_trace(
    joined_agent, capsys, close_code
):
    """The owner leaves while the owner's agent speaks, so the cut sentence enters the window of the other person's
    agent, and the turn it wakes must not join the owner's socket session."""
    member_token = joined_agent.call.token(joined_agent.member_account)
    joined_agent.call.call(member_token, JOIN_CALL, {"room": joined_agent.owner.room_id, "name": "Bach"})
    speak(joined_agent, seconds=None)

    socket_end_seen = SocketEnd()
    with configured("hopin"):
        logging.getLogger().addHandler(socket_end_seen)
        joined_agent.owner.drop(code=close_code)
        assert socket_end_seen.happened.wait(DROP_HANDLED_SECONDS), "the owner's socket did not end"
        answer = joined_agent.call.window(member_token, joined_agent.owner.room_id)
        entries = json.loads(answer["result"]["contents"][0]["text"])["entries"]
        lines = logged(capsys)

    [socket_end] = [line for line in lines if line["event"] == SOCKET_ENDED]
    [entry] = entries
    assert (entry["cut"], trace_id_of(entry["traceparent"]) != socket_end["trace_id"]) == (True, True)


async def sentence_end_in(trace, scheduler, seconds):
    """The clock moves past the sentence's end inside a unit of its own trace, as the server's timer runs it."""
    with unit_trace(trace):
        await scheduler.advance(seconds)


def test_the_agents_spoken_line_enters_its_place_with_the_trace_its_sentence_ended_in(joined_agent):
    speak(joined_agent, headers={"traceparent": a_traceparent()})
    end_trace = a_traceparent()

    joined_agent.call.client.portal.call(sentence_end_in, end_trace, joined_agent.call.scheduler, SENTENCE_SECONDS)

    [entry] = joined_agent.entries()
    assert (entry["spoken"], trace_id_of(entry["traceparent"])) == (True, trace_id_of(end_trace))


@pytest.mark.parametrize("carrier", ["headers", "meta"])
def test_a_speak_carrying_a_traceparent_after_a_heard_turn_writes_its_speak_line_in_that_trace(
    sponsored, fake_speech, turn, capsys, carrier
):
    hear_the_turn(sponsored, fake_speech, turn)
    speak_trace = a_traceparent()

    with configured("hopin"):
        speak(sponsored, **{carrier: {"traceparent": speak_trace}})

    [spoken] = [line for line in logged(capsys) if line["event"] == "speak.ended"]
    assert spoken["trace_id"] == trace_id_of(speak_trace)


def test_the_agents_sentence_after_a_heard_turn_reaches_the_speech_service_in_the_trace_its_speak_carries(
    sponsored, fake_speech, speech_origin, turn
):
    hear_the_turn(sponsored, fake_speech, turn)
    speak_trace = a_traceparent()

    speak(sponsored, headers={"traceparent": speak_trace})

    [request] = speech_requests(speech_origin)
    assert trace_id_of(header_of(request, TRACEPARENT_HEADER)) == trace_id_of(speak_trace)


def test_a_speak_without_a_traceparent_after_a_heard_turn_joins_no_turn(sponsored, fake_speech, turn, capsys):
    hear_the_turn(sponsored, fake_speech, turn)

    with configured("hopin"):
        speak(sponsored)

    [spoken] = [line for line in logged(capsys) if line["event"] == "speak.ended"]
    assert spoken["trace_id"] not in {None, turn.trace_id}


def test_the_hearing_session_opens_its_socket_to_the_speech_service_in_a_trace(sponsored, fake_speech, turn):
    hear_the_turn(sponsored, fake_speech, turn)

    assert fake_speech.next_session().headers.get(TRACEPARENT_HEADER) is not None


def test_the_hearing_hops_lines_and_its_socket_end_share_the_trace_of_its_upgrade_to_the_service(
    sponsored, fake_speech, turn, capsys
):
    with configured("hopin"):
        hear_the_turn(sponsored, fake_speech, turn)
    upgrade_trace = trace_id_of(fake_speech.next_session().headers[TRACEPARENT_HEADER])

    [socket_end] = [line for line in logged(capsys) if line["event"] == "websocket.ended"]
    assert socket_end["trace_id"] == upgrade_trace
