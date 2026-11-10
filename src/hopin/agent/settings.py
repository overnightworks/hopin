from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlsplit

from agents.config import AgentConfig  # noqa: TC002 - pydantic resolves the annotation at runtime
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# RFC 6749 section 3.3: a scope token is one or more printable ASCII characters other than space, `"` and `\`.
type ScopeToken = Annotated[str, Field(pattern=r"^[\x21\x23-\x5b\x5d-\x7e]+$")]


@dataclass(frozen=True)
class SpeechService:
    """A speech service that is configured: its origin, the voice it speaks and the scope its token carries."""

    origin: str
    voice: str
    audience_scope: str


class SpeechSettings(BaseModel):
    """The speech service the game master speaks through; without an origin the voice is off."""

    origin: str | None = None
    voice: str = Field(default="piper", min_length=1)
    audience_scope: ScopeToken | None = None

    @property
    def service(self) -> SpeechService | None:
        """The configured service, or nothing when the voice is off; the one decision of whether speech is on."""
        if self.origin is None or self.audience_scope is None:
            return None
        return SpeechService(self.origin, self.voice, self.audience_scope)

    @model_validator(mode="after")
    def _origin_is_plain_http_and_names_its_scope(self) -> SpeechSettings:
        if self.origin is None:
            return self
        if not _is_plain_http_origin(self.origin):
            raise ValueError("origin must be http://host[:port] with no path")
        if self.audience_scope is None:
            raise ValueError("audience_scope is required when origin is set")
        return self


def _is_plain_http_origin(origin: str) -> bool:
    try:
        address = urlsplit(origin)
        # Reading the port raises for one that is no number, which the speech client would raise on every call.
        _port = address.port
    except ValueError:
        return False
    return address.scheme == "http" and bool(address.hostname) and not address.path


class AgentSettings(BaseSettings):
    # Read like the server's own settings, and a refusal never echoes an input.
    model_config = SettingsConfigDict(env_prefix="HOPIN_", env_nested_delimiter="__", hide_input_in_errors=True)

    agent: AgentConfig
    speech: SpeechSettings = SpeechSettings()
