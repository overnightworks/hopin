"""CALL-61: a crash of the speech client's stream after the sound began reaches the sentence's reader as that crash."""

import pytest
from pydantic import SecretStr
from speech_client.wire import Language, VoiceId

from hopin.agent.speaking import SpeechRenderer
from hopin.agent.speech_settings import SpeechService

pytestmark = pytest.mark.asyncio


class StubRelyingParty:
    def workload_access_token(self, *, scopes):
        return SecretStr("workload-token")


async def test_call_61_a_stream_that_crashes_after_the_first_chunk_ends_the_pieces_with_that_crash():
    def crashing_stream(*_arguments, **_named):
        yield b"first"
        message = "the crash"
        raise RuntimeError(message)

    renderer = SpeechRenderer(
        SpeechService("http://speech.test", VoiceId.THORSTEN, "speech-scope"),
        deadline_seconds=1,
        cap_bytes=1024,
        relying_party=StubRelyingParty(),
        speak_stream=crashing_stream,
    )

    rendered = await renderer.stream("Guten Tag", "person-token", Language.DE, (VoiceId.THORSTEN,))

    with pytest.raises(RuntimeError, match="the crash"):
        await anext(rendered.rest)
    assert rendered.first == b"first"
