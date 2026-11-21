"""The probe's microphone sound: the question put to the agent, spoken by a local Piper voice."""

import io
import wave
from pathlib import Path

from piper import PiperVoice
from piper.config import SynthesisConfig

from hopin_probe.checks import Stage, StartFailure

QUESTION = "{name}, wie geht es dir heute?"


def question_for(agent: str) -> str:
    return QUESTION.format(name=agent)


def synthesise(voice_file: Path, sentence: str) -> bytes:
    """The sentence as a WAV. The voice's duration noise is off: with it on, a sentence pauses inside for longer than
    a turn may, and the agent would take it for two."""
    if not (voice_file.is_file() and Path(f"{voice_file}.json").is_file()):
        raise StartFailure(Stage.VOICE, "voice_missing")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        PiperVoice.load(voice_file).synthesize_wav(sentence, writer, syn_config=SynthesisConfig(noise_w_scale=0.0))
    return buffer.getvalue()
