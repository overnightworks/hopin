import asyncio
from typing import TYPE_CHECKING, assert_never

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

from hopin.agent.backend import StarterRefusedError, issuer_error

if TYPE_CHECKING:
    from starlette.requests import HTTPConnection
    from webauth.oidc import OidcRelyingParty

    from hopin.agent.backend import StarterPerson
    from hopin.agent.settings import SpeechService

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class SpeechVoice:
    """Speaks a line through the speech service, on the starter's access token and HopIn's own workload token."""

    def __init__(
        self,
        service: SpeechService,
        deadline_seconds: float,
        cap_bytes: int,
        relying_party: OidcRelyingParty,
        starter_person: StarterPerson,
    ) -> None:
        self._service = service
        self._deadline_seconds = deadline_seconds
        self._cap_bytes = cap_bytes
        self._relying_party = relying_party
        self._starter_person = starter_person

    async def speak(self, starter: HTTPConnection, text: str) -> bytes | None:
        try:
            person_token = await self._starter_person.access_token_of(starter)
        except StarterRefusedError:
            _log_failure("starter_token_refused")
            return None
        workload = await asyncio.to_thread(
            self._relying_party.workload_access_token, scopes=(self._service.audience_scope,)
        )
        if isinstance(workload, OidcRefusal):
            _log_failure("workload_token_refused", idp_error=issuer_error(workload))
            return None
        try:
            # A worker thread ends by its deadline, also when this call is cancelled.
            outcome = await asyncio.to_thread(
                speak,
                self._service.origin,
                text,
                self._service.voice,
                response_format=ResponseFormat.WAV,
                workload_token=workload.get_secret_value(),
                person_token=person_token,
                deadline_seconds=self._deadline_seconds,
                cap_bytes=self._cap_bytes,
            )
        except ValidationError:
            _log_failure("input_refused")
            return None
        return _wav_of(outcome)


def _wav_of(outcome: SpeakOutcome) -> bytes | None:
    match outcome:
        case Spoken(audio=audio):
            return audio
        case Refused(code=code):
            _log_failure("refused", code=code.value)
        case Busy():
            _log_failure("busy")
        case Cut():
            _log_failure("cut")
        case PastDeadline():
            _log_failure("past_deadline")
        case OverCap():
            _log_failure("over_cap")
        case Malformed():
            _log_failure("malformed")
        case Unreachable():
            _log_failure("unreachable")
        case _:  # pragma: no cover - the type checker proves the match exhaustive
            assert_never(outcome)
    return None


def _log_failure(outcome: str, **codes: str | None) -> None:
    # Only closed codes: the text, a token and the audio never reach a log.
    logger.info("game_voice_failed", outcome=outcome, **{name: code for name, code in codes.items() if code})
