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
from speech_client.wire import ResponseFormat
from webauth.oidc import OidcRefusal

from hopin.agent.issuer_error import issuer_error
from hopin.participation.speaking import RenderedSentence

if TYPE_CHECKING:
    from webauth.oidc import OidcRelyingParty

    from hopin.agent.hearing_token import HearingTokens
    from hopin.agent.speech_settings import SpeechService
    from hopin.participation.feature import Person

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

SPEAK_DEADLINE_SECONDS: Final = 20
SPEAK_MAX_BYTES: Final = 4 * 1024 * 1024
AGENT_SPEAK_FAILED_EVENT: Final = "agent_speak_failed"
MILLISECONDS_PER_SECOND: Final = 1000


@dataclass(frozen=True)
class SpeechRenderer:
    """Renders text to a WAV at the speech service, within a deadline and a size cap; `failed_event` names the log line
    that carries a failure's closed code."""

    service: SpeechService
    deadline_seconds: float
    cap_bytes: int
    relying_party: OidcRelyingParty
    failed_event: str

    async def wav(self, text: str, person_token: str) -> bytes | None:
        """The WAV of the text spoken on the person's token and HopIn's workload token, or nothing for any reason.

        Raises nothing but `CancelledError`; a cancelled call's worker ends within the deadline.
        """
        workload = await asyncio.to_thread(
            self.relying_party.workload_access_token, scopes=(self.service.audience_scope,)
        )
        if isinstance(workload, OidcRefusal):
            self.log_failure("workload_token_refused", idp_error=issuer_error(workload))
            return None
        try:
            # A worker thread ends by its deadline, also when this call is cancelled.
            outcome = await asyncio.to_thread(
                speak,
                self.service.origin,
                text,
                self.service.voice,
                response_format=ResponseFormat.WAV,
                workload_token=workload.get_secret_value(),
                person_token=person_token,
                deadline_seconds=self.deadline_seconds,
                cap_bytes=self.cap_bytes,
            )
        except ValidationError:
            self.log_failure("input_refused")
            return None
        return self._wav_of(outcome)

    def log_failure(self, outcome: str, **codes: str | None) -> None:
        logger.info(self.failed_event, outcome=outcome, **{name: code for name, code in codes.items() if code})

    def _wav_of(self, outcome: SpeakOutcome) -> bytes | None:
        match outcome:
            case Spoken(audio=audio):
                return audio
            case Refused(code=code):
                self.log_failure("refused", code=code.value)
            case Busy():
                self.log_failure("busy")
            case Cut():
                self.log_failure("cut")
            case PastDeadline():
                self.log_failure("past_deadline")
            case OverCap():
                self.log_failure("over_cap")
            case Malformed():
                self.log_failure("malformed")
            case Unreachable():
                self.log_failure("unreachable")
            case _:  # pragma: no cover - the type checker proves the match exhaustive
                assert_never(outcome)
        return None


def duration_ms_of(wav: bytes) -> int | None:
    """How long the WAV plays, rounded up to a millisecond, or nothing when it cannot be read or holds no sound."""
    try:
        with wave.open(io.BytesIO(wav)) as reader:
            frames, rate = reader.getnframes(), reader.getframerate()
    except wave.Error, EOFError:
        return None
    return math.ceil(frames * MILLISECONDS_PER_SECOND / rate) or None


class AgentVoice:
    """Renders an agent's sentence on the token HopIn holds for the agent."""

    def __init__(self, renderer: SpeechRenderer, tokens: HearingTokens) -> None:
        self._renderer = renderer
        self._tokens = tokens

    async def render(self, agent: Person, text: str) -> RenderedSentence | None:
        """The sentence's sound and length, or nothing when no token is held, speech fails or its WAV cannot be read."""
        token = self._tokens.held(agent)
        if token is None:
            self._renderer.log_failure("no_token")
            return None
        wav = await self._renderer.wav(text, token.get_secret_value())
        if wav is None:
            return None
        duration_ms = duration_ms_of(wav)
        if duration_ms is None:
            self._renderer.log_failure("unreadable_audio")
            return None
        return RenderedSentence(wav, duration_ms)
