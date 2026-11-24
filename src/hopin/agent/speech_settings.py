from dataclasses import dataclass
from typing import Annotated, ClassVar, Final
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import SettingsConfigDict
from speech_client.wire import VOICES, Language, VoiceId

from hopin.agent.fleet_keys import FleetKeyedSettings
from hopin.words import SOURCE_LANGUAGE

# RFC 6749 section 3.3: a scope token is one or more printable ASCII characters other than space, `"` and `\`.
type ScopeToken = Annotated[str, Field(pattern=r"^[\x21\x23-\x5b\x5d-\x7e]+$")]


# The fleet's key for the speech service's audience scope, which the platform's Deployment hands every app.
SPEECH_SCOPE_KEY: Final = "REALM_SPEECH_SCOPE"

# The language of HopIn's page, which an agent's sentence without a language is spoken in.
PAGE_LANGUAGE: Final = Language(SOURCE_LANGUAGE)


@dataclass(frozen=True)
class SpeechService:
    """A speech service that is configured: its origin, the voice it speaks and the scope its token carries."""

    origin: str
    voice: VoiceId
    audience_scope: str


class SpeechSettings(FleetKeyedSettings):
    """The speech service HopIn speaks and hears through; without an origin the voice is off, and hearing and agents'
    speaking are off until each is turned on."""

    # Read like the server's own settings, and a refusal never echoes an input.
    model_config = SettingsConfigDict(env_prefix="HOPIN_SPEECH__", hide_input_in_errors=True)

    environment_keys: ClassVar = {("audience_scope",): SPEECH_SCOPE_KEY}

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
        check_origin_names_its_scope(
            self.model_config["env_prefix"], self.origin, SPEECH_SCOPE_KEY, self.audience_scope
        )
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
            prefix = self.model_config["env_prefix"]
            raise ValueError(
                f"{prefix}ORIGIN and {SPEECH_SCOPE_KEY} are required when {prefix}HEARING or {prefix}SPEAKING is on"
            )
        return self


def check_origin_names_its_scope(prefix: str, origin: str | None, scope_key: str, audience_scope: str | None) -> None:
    """Refuse an origin that is no plain http origin, and an origin without its audience scope; a service without an
    origin is off and needs neither. The message names the settings by `prefix` and `scope_key` and never echoes the
    value."""
    if origin is None:
        return
    if not _is_plain_http_origin(origin):
        raise ValueError(f"{prefix}ORIGIN must be http://host[:port] with no path, query or fragment")
    if audience_scope is None:
        raise ValueError(f"{scope_key} is required when {prefix}ORIGIN is set")


def _is_plain_http_origin(origin: str) -> bool:
    try:
        address = urlsplit(origin)
        # Reading the port raises for one that is no number, which the speech client would raise on every call.
        _port = address.port
    except ValueError:
        return False
    # An empty query or fragment parses to nothing, so the marks are looked for, as the services' clients do.
    return (
        address.scheme == "http"
        and bool(address.hostname)
        and not address.path
        and "?" not in origin
        and "#" not in origin
    )
