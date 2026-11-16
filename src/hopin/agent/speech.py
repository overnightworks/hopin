import asyncio
import logging
from typing import TYPE_CHECKING, Final

from logline import StepTimer

from hopin.agent.backend import StarterRefusedError
from hopin.agent.speaking import SPEECH_STREAM, SpeechRenderer
from hopin.agent.speech_settings import PAGE_LANGUAGE
from hopin.game.voice import VOICE_DEADLINE_CUT
from hopin.participation.vocabulary import VoiceFailure

if TYPE_CHECKING:
    from starlette.requests import HTTPConnection
    from webauth.oidc import OidcRelyingParty

    from hopin.agent.backend import StarterPerson
    from hopin.agent.speech_settings import SpeechService

_STEPS: Final = logging.getLogger(__name__)
STARTER_TOKEN_REFUSED_REASON: Final = "starter_token_refused"  # noqa: S105 - name of a refusal and no credential


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
        self._renderer = SpeechRenderer(service, deadline_seconds, cap_bytes, relying_party, SPEECH_STREAM)
        self._starter_person = starter_person

    async def speak(self, starter: HTTPConnection, text: str) -> bytes | None:
        async with StepTimer("game_voice.ended", _STEPS) as step:
            try:
                return await self._wav(starter, text, step)
            except asyncio.CancelledError as cancelled:  # NOSONAR(S7497) the deadline's cut is answered, not raised
                if cancelled.args != (VOICE_DEADLINE_CUT,):
                    raise
                # The table's deadline cut the call; the step ends refused, which a propagated cancel cannot.
                step.refuse(VoiceFailure.PAST_DEADLINE.value)
                return None

    async def _wav(self, starter: HTTPConnection, text: str, step: StepTimer) -> bytes | None:
        try:
            person_token = await self._starter_person.access_token_of(starter)
        except StarterRefusedError:
            step.refuse(STARTER_TOKEN_REFUSED_REASON)
            return None
        wav = await self._renderer.wav(text, person_token, PAGE_LANGUAGE)
        if isinstance(wav, VoiceFailure):
            step.refuse(wav.value)
            return None
        return wav
