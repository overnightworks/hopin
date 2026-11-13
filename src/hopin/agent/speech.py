from typing import TYPE_CHECKING, Final

from hopin.agent.backend import StarterRefusedError
from hopin.agent.speaking import SpeechRenderer

if TYPE_CHECKING:
    from starlette.requests import HTTPConnection
    from webauth.oidc import OidcRelyingParty

    from hopin.agent.backend import StarterPerson
    from hopin.agent.speech_settings import SpeechService

GAME_VOICE_FAILED_EVENT: Final = "game_voice_failed"


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
        self._renderer = SpeechRenderer(service, deadline_seconds, cap_bytes, relying_party, GAME_VOICE_FAILED_EVENT)
        self._starter_person = starter_person

    async def speak(self, starter: HTTPConnection, text: str) -> bytes | None:
        try:
            person_token = await self._starter_person.access_token_of(starter)
        except StarterRefusedError:
            self._renderer.log_failure("starter_token_refused")
            return None
        return await self._renderer.wav(text, person_token)
