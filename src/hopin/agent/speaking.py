"""Speaking through the speech service: the one call that renders a sentence to a WAV on HopIn's workload token and
the caller's own token, and the agent's renderer, which speaks on the token HopIn holds for the agent.

The text, the sound and a token reach no log: a failure logs one closed code."""

import asyncio
import io
import math
import wave
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, assert_never

import structlog
from pydantic import ValidationError
from speech_client.speak import (
    Busy,
    Cut,
    Malformed,
    OverCap,
    PastDeadline,
    Refused,
    SpeakOutcome,
    Spoken,
    Unreachable,
    speak,
)
from speech_client.wire import VOICES, Language, RefusalCode, ResponseFormat, VoiceId
from webauth.oidc import OidcRefusal

from hopin.agent.issuer_error import issuer_error
from hopin.agent.speech_settings import PAGE_LANGUAGE, SpeechService
from hopin.participation.speaking import RenderedSentence
from hopin.participation.vocabulary import VoiceFailure

if TYPE_CHECKING:
    from webauth.oidc import OidcRelyingParty

    from hopin.agent.hearing_token import HearingTokens
    from hopin.participation.participants import Person

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

SPEAK_DEADLINE_SECONDS: Final = 20
SPEAK_MAX_BYTES: Final = 4 * 1024 * 1024
MILLISECONDS_PER_SECOND: Final = 1000
# The size a streaming WAV declares for its sound, which is not known when the first byte leaves.
STREAMING_DATA_SIZE: Final = 0xFFFFFFFF


class StyleUnavailableError(Exception):
    """The voice refuses the style of a sentence: it takes no style, or the style is too long, has no letter or is one
    the service does not support."""


@dataclass(frozen=True)
class SpeechRenderer:
    """Renders text to a WAV at the speech service, within a deadline and a size cap; `failed_event` names the log line
    that carries a failure's closed code."""

    service: SpeechService
    deadline_seconds: float
    cap_bytes: int
    relying_party: OidcRelyingParty
    failed_event: str

    async def wav(
        self,
        text: str,
        person_token: str,
        language: Language,
        voice: VoiceId | None = None,
        style: str | None = None,
    ) -> bytes | VoiceFailure:
        """The WAV of the text spoken in `language` on the person's token and HopIn's workload token in `voice`, the
        service's configured one when none, in the `style` handed to the service unchanged, or why the service made
        none.

        Raises `StyleUnavailableError` when the voice refuses the style, and otherwise nothing but `CancelledError`; a
        cancelled call's worker ends within the deadline.
        """
        workload = await asyncio.to_thread(
            self.relying_party.workload_access_token, scopes=(self.service.audience_scope,)
        )
        if isinstance(workload, OidcRefusal):
            return self._failed(VoiceFailure.WORKLOAD_TOKEN_REFUSED, idp_error=issuer_error(workload))
        try:
            # A worker thread ends by its deadline, also when this call is cancelled.
            outcome = await asyncio.to_thread(
                speak,
                self.service.origin,
                text,
                self.service.voice if voice is None else voice,
                language=language,
                instructions=style,
                response_format=ResponseFormat.WAV,
                workload_token=workload.get_secret_value(),
                person_token=person_token,
                deadline_seconds=self.deadline_seconds,
                cap_bytes=self.cap_bytes,
            )
        except ValidationError as refusal:
            if any(error["loc"] == ("instructions",) for error in refusal.errors()):
                raise StyleUnavailableError from None
            return self._failed(VoiceFailure.INPUT_REFUSED)
        return self._wav_of(outcome)

    def log_failure(self, outcome: str, **codes: str | None) -> None:
        logger.info(self.failed_event, outcome=outcome, **{name: code for name, code in codes.items() if code})

    def _failed(self, failure: VoiceFailure, **codes: str | None) -> VoiceFailure:
        self.log_failure(failure.value, **codes)
        return failure

    def _wav_of(self, outcome: SpeakOutcome) -> bytes | VoiceFailure:
        match outcome:
            case Spoken(audio=audio):
                return audio
            case Refused(code=RefusalCode.INVALID_REQUEST, param="instructions"):
                raise StyleUnavailableError
            case Refused(code=code):
                return self._failed(VoiceFailure.REFUSED, code=code.value)
            case Busy():
                return self._failed(VoiceFailure.BUSY)
            case Cut():
                return self._failed(VoiceFailure.CUT)
            case PastDeadline():
                return self._failed(VoiceFailure.PAST_DEADLINE)
            case OverCap():
                return self._failed(VoiceFailure.OVER_CAP)
            case Malformed():
                return self._failed(VoiceFailure.MALFORMED)
            case Unreachable():
                return self._failed(VoiceFailure.UNREACHABLE)
            case _:  # pragma: no cover - the type checker proves the match exhaustive
                assert_never(outcome)


def duration_ms_of(wav: bytes) -> int | None:
    """How long the WAV plays, rounded up to a millisecond, or nothing when it cannot be read, names no sample rate,
    holds no sound or holds fewer frames than its header declares. A streaming WAV declares no size, so the frames it
    holds are its length."""
    try:
        with wave.open(io.BytesIO(wav)) as reader:
            declared, rate = reader.getnframes(), reader.getframerate()
            frame_bytes = reader.getnchannels() * reader.getsampwidth()
            held = len(reader.readframes(declared)) // frame_bytes
    except wave.Error, EOFError:
        return None
    if rate <= 0 or (held < declared and declared != STREAMING_DATA_SIZE // frame_bytes):
        return None
    return math.ceil(held * MILLISECONDS_PER_SECOND / rate) or None


def known_voice(name: str | None) -> str | None:
    """The voice the speech service's catalogue names, or nothing for no name and for one it does not name."""
    try:
        return None if name is None else VoiceId(name).value
    except ValueError:
        return None


class LanguageUnavailableError(Exception):
    """The language of a sentence is none the voice that would speak it speaks, or none the speech service knows."""


class AgentVoice:
    """Renders an agent's sentence on the token HopIn holds for the agent."""

    def __init__(self, renderer: SpeechRenderer, tokens: HearingTokens) -> None:
        self._renderer = renderer
        self._tokens = tokens

    async def render(
        self, agent: Person, text: str, voice: str | None, *, language: str | None = None, style: str | None = None
    ) -> RenderedSentence | VoiceFailure | None:
        """The sentence's sound and length in the agent's `voice`, a catalogue voice its accept named or none for the
        configured one, spoken in the `language` it names or in the page's when it names none and in the `style` it
        names, which speech alone checks; why the voice failed when speech fails or its WAV cannot be read; nothing
        when no token is held, which is no failure of the voice.

        Raises `LanguageUnavailableError`, before any request to speech, for a language the voice does not speak, and
        `StyleUnavailableError` for a style the voice refuses."""
        spoken_voice = self._renderer.service.voice if voice is None else VoiceId(voice)
        spoken_language = _spoken_language(language, spoken_voice)
        token = self._tokens.held(agent)
        if token is None:
            self._renderer.log_failure("no_token")
            return None
        wav = await self._renderer.wav(text, token.get_secret_value(), spoken_language, spoken_voice, style)
        if isinstance(wav, VoiceFailure):
            return wav
        duration_ms = duration_ms_of(wav)
        if duration_ms is None:
            self._renderer.log_failure(VoiceFailure.UNREADABLE_AUDIO.value)
            return VoiceFailure.UNREADABLE_AUDIO
        return RenderedSentence(wav, duration_ms)


def _spoken_language(code: str | None, voice: VoiceId) -> Language:
    try:
        language = PAGE_LANGUAGE if code is None else Language(code)
    except ValueError:
        raise LanguageUnavailableError from None
    if language not in VOICES[voice].languages:
        raise LanguageUnavailableError
    return language
