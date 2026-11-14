"""Speaking through the speech service: the calls that render a sentence to a WAV on HopIn's workload token and the
caller's own token, whole for the game master and as a stream for an agent's renderer, which speaks on the token HopIn
holds for the agent.

The text, the sound and a token reach no log: a failure logs one closed code."""

import asyncio
import math
import threading
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, assert_never, cast

import structlog
from pydantic import ValidationError
from speech_client.health import fetch_health
from speech_client.speak import (
    Busy,
    Cut,
    Finished,
    Malformed,
    OverCap,
    PastDeadline,
    Refused,
    Spoken,
    Unreachable,
    speak,
)
from speech_client.voice_preference import VoiceChosen, speak_stream_with_preference
from speech_client.wire import VOICES, Health, Language, RefusalCode, ResponseFormat, VoiceId
from webauth.oidc import OidcRefusal

from hopin.agent.issuer_error import issuer_error
from hopin.agent.speech_settings import PAGE_LANGUAGE, SpeechService
from hopin.participation.speaking import RenderedSentence, SoundLength, SoundPiece
from hopin.participation.vocabulary import VoiceFailure

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Generator, Sequence

    from speech_client.speak import StreamFailure
    from speech_client.voice_preference import PreferenceStreamItem
    from webauth.oidc import OidcRelyingParty

    from hopin.agent.hearing_token import HearingTokens
    from hopin.participation.participants import Person

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

SPEAK_DEADLINE_SECONDS: Final = 20
SPEAK_MAX_BYTES: Final = 4 * 1024 * 1024
HEALTH_DEADLINE_SECONDS: Final = 5
HEALTH_MAX_BYTES: Final = 64 * 1024
MILLISECONDS_PER_SECOND: Final = 1000


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

    async def wav(self, text: str, person_token: str, language: Language) -> bytes | VoiceFailure:
        """The WAV of the text spoken in `language` in the service's configured voice on the person's token and HopIn's
        workload token, or why the service made none.

        Raises nothing but `CancelledError`; a cancelled call's worker ends within the deadline."""
        workload = await self._workload_token()
        if isinstance(workload, VoiceFailure):
            return workload
        try:
            # A worker thread ends by its deadline, also when this call is cancelled.
            outcome = await asyncio.to_thread(
                speak,
                self.service.origin,
                text,
                self.service.voice,
                language=language,
                response_format=ResponseFormat.WAV,
                workload_token=workload,
                person_token=person_token,
                deadline_seconds=self.deadline_seconds,
                cap_bytes=self.cap_bytes,
            )
        except ValidationError:
            return self._failed(VoiceFailure.INPUT_REFUSED)
        return outcome.audio if isinstance(outcome, Spoken) else self._failed_by(outcome)

    async def stream(
        self,
        text: str,
        person_token: str,
        language: Language,
        voices: Sequence[VoiceId],
        style: str | None = None,
    ) -> RenderedSentence | VoiceFailure:
        """The sentence's first chunk and the pieces after it, once the first of the `voices` the service offers ready
        has begun to speak the text in `language` on the person's token and HopIn's workload token, with the `style`
        handed to the service unchanged, or why the service made none. The deadline bounds the whole stream, and the
        pieces are read at once, so it covers the rendering only.

        Raises `StyleUnavailableError` when the voice refuses the style, and otherwise nothing but `CancelledError`;
        closing the pieces, or a cancelled call, ends the worker at its next chunk or the deadline."""
        workload = await self._workload_token()
        if isinstance(workload, VoiceFailure):
            return workload
        try:
            items = speak_stream_with_preference(
                self.service.origin,
                text,
                voices,
                language=language,
                instructions=style,
                workload_token=workload,
                person_token=person_token,
                deadline_seconds=self.deadline_seconds,
                cap_bytes=self.cap_bytes,
            )
        except ValidationError as refusal:
            if any(error["loc"] == ("instructions",) for error in refusal.errors()):
                raise StyleUnavailableError from None
            return self._failed(VoiceFailure.INPUT_REFUSED)
        pieces = self._pieces(items)
        first = await anext(pieces)
        # The client ends a stream with a length only after a chunk.
        return first if isinstance(first, VoiceFailure) else RenderedSentence(cast("bytes", first), pieces)

    def log_failure(self, outcome: str, **codes: str | None) -> None:
        logger.info(self.failed_event, outcome=outcome, **{name: code for name, code in codes.items() if code})

    async def _workload_token(self) -> str | VoiceFailure:
        workload = await asyncio.to_thread(
            self.relying_party.workload_access_token, scopes=(self.service.audience_scope,)
        )
        if isinstance(workload, OidcRefusal):
            return self._failed(VoiceFailure.WORKLOAD_TOKEN_REFUSED, idp_error=issuer_error(workload))
        return workload.get_secret_value()

    async def _pieces(self, items: Generator[PreferenceStreamItem]) -> AsyncGenerator[SoundPiece]:
        """The stream's chunks, read in one worker thread as they arrive, and its end: the sound's length, or the
        failure that broke it off."""
        arrived: asyncio.Queue[PreferenceStreamItem | Exception] = asyncio.Queue()
        stopped = threading.Event()
        loop = asyncio.get_running_loop()
        loop.run_in_executor(
            None, _read, items, lambda item: loop.call_soon_threadsafe(arrived.put_nowait, item), stopped
        )
        try:
            while True:
                item = await arrived.get()
                if isinstance(item, Exception):
                    raise item
                if isinstance(item, bytes):
                    yield item
                elif not isinstance(item, VoiceChosen):
                    yield self._end_of(item)
                    return
        finally:
            stopped.set()

    def _end_of(self, ending: Finished | StreamFailure) -> SoundLength | VoiceFailure:
        if isinstance(ending, Finished):
            duration_ms = math.ceil(ending.seconds * MILLISECONDS_PER_SECOND)
            return SoundLength(duration_ms) if duration_ms > 0 else self._failed(VoiceFailure.UNREADABLE_AUDIO)
        if (
            isinstance(ending, Refused)
            and ending.code is RefusalCode.INVALID_REQUEST
            and ending.param == "instructions"
        ):
            # A refusal comes before any sound, so only a sentence's first piece raises.
            raise StyleUnavailableError
        return self._failed_by(ending)

    def _failed(self, failure: VoiceFailure, **codes: str | None) -> VoiceFailure:
        self.log_failure(failure.value, **codes)
        return failure

    def _failed_by(self, outcome: StreamFailure) -> VoiceFailure:
        match outcome:
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


def _read(
    items: Generator[PreferenceStreamItem],
    deliver: Callable[[PreferenceStreamItem | Exception], object],
    stopped: threading.Event,
) -> None:
    """Hand each item of the blocking stream over as it arrives, in a worker thread, until the stream ends or `stopped`
    is set, which is seen at the next item; a break in the stream is handed over too."""
    try:
        with closing(items):
            for item in items:
                if stopped.is_set():
                    return
                deliver(item)
    except Exception as failure:
        deliver(failure)


def known_voices(names: Sequence[str] | None) -> tuple[str, ...]:
    """The voices of the list the speech service's catalogue names, in the list's order; the names it does not name
    are left out, and no list at all is no voice."""
    return tuple(VoiceId(name).value for name in names or () if name in VoiceId)


class LanguageUnavailableError(Exception):
    """The language of a sentence is none the voice that would speak it speaks, or none the speech service knows."""


class AgentVoice:
    """Renders an agent's sentence on the token HopIn holds for the agent."""

    def __init__(self, renderer: SpeechRenderer, tokens: HearingTokens) -> None:
        self._renderer = renderer
        self._tokens = tokens

    async def render(
        self,
        agent: Person,
        text: str,
        voices: tuple[str, ...],
        *,
        language: str | None = None,
        style: str | None = None,
    ) -> RenderedSentence | VoiceFailure | None:
        """The sentence's first chunk, and the stream after it, in the first of the agent's `voices`, the catalogue
        voices its accept named, that speech offers ready, or in the configured voice for none; spoken in the
        `language` it names or in the page's when it names none and in the `style` it names, which speech alone
        checks; why the voice failed when speech fails or offers none of the voices before any sound; nothing when no
        token is held, which is no failure of the voice. A style goes only to a voice that takes one; a voice of the
        list that takes none speaks the sentence plain.

        Raises `LanguageUnavailableError` and `StyleUnavailableError`, before any request to speech, for a language none
        of the voices speaks and for a style none of them takes; speech's own refusal of the style raises the latter
        too."""
        preference = self._preference(voices)
        spoken_language = _spoken_language(language, preference)
        if style is not None and not any(VOICES[voice].takes_instructions for voice in preference):
            raise StyleUnavailableError
        token = self._tokens.held(agent)
        if token is None:
            self._renderer.log_failure("no_token")
            return None
        return await self._renderer.stream(text, token.get_secret_value(), spoken_language, preference, style)

    async def offers(self, voices: tuple[str, ...]) -> bool:
        """Whether speech's health offers one of the agent's `voices`, the configured voice for none, ready now; no
        when health cannot be read."""
        health = await asyncio.to_thread(
            fetch_health,
            self._renderer.service.origin,
            deadline_seconds=HEALTH_DEADLINE_SECONDS,
            cap_bytes=HEALTH_MAX_BYTES,
        )
        return isinstance(health, Health) and any(
            (offered := health.speaking.voices.get(voice)) is not None and offered.ready
            for voice in self._preference(voices)
        )

    def _preference(self, voices: tuple[str, ...]) -> tuple[VoiceId, ...]:
        return tuple(VoiceId(voice) for voice in voices) or (self._renderer.service.voice,)


def _spoken_language(code: str | None, preference: Sequence[VoiceId]) -> Language:
    try:
        language = PAGE_LANGUAGE if code is None else Language(code)
    except ValueError:
        raise LanguageUnavailableError from None
    if not any(language in VOICES[voice].languages for voice in preference):
        raise LanguageUnavailableError
    return language
