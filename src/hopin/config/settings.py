from pathlib import Path
from typing import Annotated, Self

from pydantic import AfterValidator, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from hopin.config.constants import (
    AGENT_SERVICE_URL,
    DEFAULT_HOST,
    DEFAULT_PORT,
    MAX_PARTICIPANTS_PER_ROOM,
    MIN_PARTICIPANTS_PER_ROOM,
    OIDC_CLIENT_ID,
    STUN_SERVER_URL,
    TurnProvider,
)


def _require_no_trailing_slash(issuer: str) -> str:
    if issuer.endswith("/"):
        raise ValueError("must not end with a slash")
    return issuer


def _require_origin_without_path(origin: str) -> str:
    if origin.removeprefix("https://").removeprefix("http://").partition("/")[1]:
        raise ValueError("must be an origin without a path")
    return origin


def _require_absolute_path(path: Path) -> Path:
    if not path.is_absolute():
        raise ValueError("must be an absolute path")
    return path


IssuerUrl = Annotated[str, AfterValidator(_require_no_trailing_slash)]
OriginUrl = Annotated[str, AfterValidator(_require_origin_without_path)]
AbsolutePath = Annotated[Path, AfterValidator(_require_absolute_path)]


class ServerSettings(BaseSettings):
    # The sign-in settings are secrets or name them, so a refusal never echoes an input.
    model_config = SettingsConfigDict(env_prefix="HOPIN_", hide_input_in_errors=True)

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    stun_server: str = STUN_SERVER_URL
    max_participants_per_room: int = Field(
        default=MAX_PARTICIPANTS_PER_ROOM, ge=MIN_PARTICIPANTS_PER_ROOM, le=MAX_PARTICIPANTS_PER_ROOM
    )
    oidc_issuer: IssuerUrl
    oidc_client_id: str = OIDC_CLIENT_ID
    oidc_client_secret_file: AbsolutePath = Field(repr=False)
    public_origin: OriginUrl
    session_secret_file: AbsolutePath = Field(repr=False)
    # Addresses and CIDR blocks, comma-separated, of the proxies allowed to name the client in X-Forwarded-For.
    trusted_proxies: str = ""
    turn_provider: TurnProvider = TurnProvider.OFF
    turn_key_id_file: AbsolutePath | None = Field(default=None, repr=False)
    turn_api_token_file: AbsolutePath | None = Field(default=None, repr=False)
    agent_service_url: OriginUrl = AGENT_SERVICE_URL

    @model_validator(mode="after")
    def _require_the_turn_key_files_when_turn_is_on(self) -> Self:
        if self.turn_provider is TurnProvider.OFF:
            return self
        missing = [
            setting
            for setting, path in [
                ("turn_key_id_file", self.turn_key_id_file),
                ("turn_api_token_file", self.turn_api_token_file),
            ]
            if path is None
        ]
        if missing:
            raise ValueError(f"{' and '.join(missing)} must be set when turn_provider is {self.turn_provider}")
        return self
