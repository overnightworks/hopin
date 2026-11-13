from dataclasses import dataclass
from typing import Annotated, Final
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from speech_client.wire import VOICES, Language, VoiceId

from hopin.words import SOURCE_LANGUAGE

# RFC 6749 section 3.3: a scope token is one or more printable ASCII characters other than space, `"` and `\`.
type ScopeToken = Annotated[str, Field(pattern=r"^[\x21\x23-\x5b\x5d-\x7e]+$")]


# The language of HopIn's page, which the game master's lines and an agent's sentence without a language are spoken in.
PAGE_LANGUAGE: Final = Language(SOURCE_LANGUAGE)


@dataclass(frozen=True)
class SpeechService:
    """A speech service that is configured: its origin, the voice it speaks and the scope its token carries."""

    origin: str
    voice: VoiceId
    audience_scope: str


class SpeechSettings(BaseSettings):
    """The speech service HopIn speaks and hears through; without an origin the voice is off, and hearing and agents'
    speaking are off until each is turned on."""

    # Read like the server's own settings, and a refusal never echoes an input.
    model_config = SettingsConfigDict(env_prefix="HOPIN_SPEECH__", hide_input_in_errors=True)

    origin: str | None = None
    voice: VoiceId = VoiceId.THORSTEN
    audience_scope: ScopeToken | None = None
    hearing: bool = False
    speaking: bool = False

    @property
    def service(self) -> SpeechService | None:
        """The configured service, or nothing when the voice is off; the one decision of whether speech is on."""
        if self.origin is None or self.audience_scope is None:
            return None
        return SpeechService(self.origin, self.voice, self.audience_scope)

    @property
    def hearing_service(self) -> SpeechService | None:
        """The service agents hear through, or nothing while hearing is off; the one decision of whether it is on."""
        return self.service if self.hearing else None

    @property
    def speaking_service(self) -> SpeechService | None:
        """The service agents speak through, or nothing while speaking is off; the one decision of whether it is on."""
        return self.service if self.speaking else None

    @model_validator(mode="after")
    def _origin_is_plain_http_and_names_its_scope(self) -> SpeechSettings:
        if self.origin is None:
            return self
        if not _is_plain_http_origin(self.origin):
            raise ValueError("origin must be http://host[:port] with no path")
        if self.audience_scope is None:
            raise ValueError("audience_scope is required when origin is set")
        return self

    @model_validator(mode="after")
    def _voice_speaks_the_page_language(self) -> SpeechSettings:
        if PAGE_LANGUAGE not in VOICES[self.voice].languages:
            setting = f"{self.model_config['env_prefix']}VOICE"
            raise ValueError(f"{setting} must name a voice that speaks the page's language, German")
        return self

    @model_validator(mode="after")
    def _hearing_and_speaking_need_a_service(self) -> SpeechSettings:
        if (self.hearing or self.speaking) and self.service is None:
            raise ValueError("origin and audience_scope are required when hearing or speaking is on")
        return self


def _is_plain_http_origin(origin: str) -> bool:
    try:
        address = urlsplit(origin)
        # Reading the port raises for one that is no number, which the speech client would raise on every call.
        _port = address.port
    except ValueError:
        return False
    return address.scheme == "http" and bool(address.hostname) and not address.path
