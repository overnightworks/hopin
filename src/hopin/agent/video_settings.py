from dataclasses import dataclass

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from hopin.agent.speech_settings import ScopeToken, check_origin_names_its_scope


@dataclass(frozen=True)
class VideoService:
    """A video service that is configured: its origin and the scope its token carries."""

    origin: str
    audience_scope: str


class VideoSettings(BaseSettings):
    """The video service HopIn opens an agent's video session on; without an origin no video code runs."""

    # Read like the server's own settings, and a refusal never echoes an input.
    model_config = SettingsConfigDict(env_prefix="HOPIN_VIDEO__", hide_input_in_errors=True)

    origin: str | None = None
    audience_scope: ScopeToken | None = None

    @property
    def service(self) -> VideoService | None:
        """The configured service, or nothing when the video is off; the one decision of whether it is on."""
        if self.origin is None or self.audience_scope is None:
            return None
        return VideoService(self.origin, self.audience_scope)

    @model_validator(mode="after")
    def _origin_is_plain_http_and_names_its_scope(self) -> VideoSettings:
        check_origin_names_its_scope(self.model_config["env_prefix"], self.origin, self.audience_scope)
        return self
