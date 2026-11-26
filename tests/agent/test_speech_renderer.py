"""CALL-61: a crash of the speech client's stream after the sound began reaches the sentence's reader as that crash.

The worker that reads the stream runs in the trace of the turn that asked for the sentence."""

import secrets

import pytest
from logline import current_traceparent, unit_trace
from logline.testing import configured
from pydantic import SecretStr
from speech_client.wire import Language, VoiceId

from hopin.agent.speaking import SpeechRenderer
from hopin.agent.speech_settings import SpeechService

pytestmark = pytest.mark.asyncio


class StubRelyingParty:
    def workload_access_token(self, *, scopes):
        return SecretStr("workload-token")


def renderer_of(speak_stream):
    return SpeechRenderer(
        SpeechService("http://speech.test", VoiceId.THORSTEN, "speech-scope"),
        deadline_seconds=1,
        cap_bytes=1024,
        relying_party=StubRelyingParty(),
        speak_stream=speak_stream,
    )


async def test_the_worker_reading_the_stream_runs_in_the_trace_of_the_turn_that_asked_for_the_sentence():
    seen = []

    def stream_noting_its_trace(*_arguments, **_named):
        seen.append(current_traceparent())
        yield b"first"

    trace_id = secrets.token_hex(16)

    with configured("hopin"), unit_trace(f"00-{trace_id}-{secrets.token_hex(8)}-01"):
        await renderer_of(stream_noting_its_trace).stream("Guten Tag", "person-token", Language.DE, (VoiceId.THORSTEN,))

    assert [traceparent.split("-")[1] for traceparent in seen] == [trace_id]


async def test_call_61_a_stream_that_crashes_after_the_first_chunk_ends_the_pieces_with_that_crash():
    def crashing_stream(*_arguments, **_named):
        yield b"first"
        message = "the crash"
        raise RuntimeError(message)

    renderer = renderer_of(crashing_stream)

    rendered = await renderer.stream("Guten Tag", "person-token", Language.DE, (VoiceId.THORSTEN,))

    with pytest.raises(RuntimeError, match="the crash"):
        await anext(rendered.rest)
    assert rendered.first == b"first"
