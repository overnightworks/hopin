from pathlib import Path
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import AfterValidator, Field, RedisDsn, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from hopin.config.constants import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    MAX_PARTICIPANTS_PER_ROOM,
    MIN_PARTICIPANTS_PER_ROOM,
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


def _require_postgres_url_without_password(url: str) -> str:
    try:
        parsed = urlsplit(url)
        parsed.port  # noqa: B018 - reading it raises for a malformed port
    except ValueError:
        # The parser's messages quote parts of the URL, which may hold the password.
        raise ValueError("must be a postgresql URL") from None
    # A colon with no digits after it names a port that is empty, which the engine refuses with a traceback.
    if parsed.scheme != "postgresql" or parsed.netloc.endswith(":"):
        raise ValueError("must be a postgresql URL")
    if not (parsed.hostname and parsed.username and parsed.path.removeprefix("/")):
        raise ValueError("must name a host, a user and a database")
    if parsed.password is not None:
        raise ValueError("must not carry a password")
    # libpq reads query parameters over the authority, so any of them can redirect the password; SQLAlchemy reads a `?`
    # after a `#` as one, which urlsplit calls part of the fragment.
    if "?" in url or "#" in url:
        raise ValueError("must not carry query parameters")
    return url


IssuerUrl = Annotated[str, AfterValidator(_require_no_trailing_slash)]
OriginUrl = Annotated[str, AfterValidator(_require_origin_without_path)]
AbsolutePath = Annotated[Path, AfterValidator(_require_absolute_path)]
PasswordlessPostgresUrl = Annotated[str, AfterValidator(_require_postgres_url_without_password)]


class DatabaseSettings(BaseSettings):
    """The settings `hopin migrate` needs, so it runs before any other setting or the page build is read."""

    # The settings are secrets or name them, so a refusal never echoes an input.
    model_config = SettingsConfigDict(env_prefix="HOPIN_", hide_input_in_errors=True)

    # Names the database without its password, which the file holds.
    database_url: PasswordlessPostgresUrl = Field(repr=False)
    database_password_file: AbsolutePath = Field(repr=False)


class RealmSettings(BaseSettings):
    """The names of HopIn's objects in the Keycloak realm, which the platform's Deployment hands every app under the
    same `REALM_` keys; none has a default, and an empty value counts as missing."""

    model_config = SettingsConfigDict(env_prefix="REALM_", env_ignore_empty=True, hide_input_in_errors=True)

    # The client HopIn signs people in as.
    client_id: str = Field(min_length=1)
    # The group whose members sign in, and the group of those who administer.
    login_group: str = Field(min_length=1)
    admin_group: str = Field(min_length=1)
    # The group whose members' agents may reach the MCP endpoint, and the scope and audience their token carries.
    mcp_group: str = Field(min_length=1)
    mcp_scope: str = Field(min_length=1)
    # The group of agents' own accounts, admitted from any client, and the public client an agent signs in through.
    agent_group: str = Field(min_length=1)
    mcp_client_id: str = Field(min_length=1)


class ServerSettings(DatabaseSettings):
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    stun_server: str = STUN_SERVER_URL
    max_participants_per_room: int = Field(
        default=MAX_PARTICIPANTS_PER_ROOM, ge=MIN_PARTICIPANTS_PER_ROOM, le=MAX_PARTICIPANTS_PER_ROOM
    )
    oidc_issuer: IssuerUrl
    oidc_client_secret_file: AbsolutePath = Field(repr=False)
    public_origin: OriginUrl
    session_secret_file: AbsolutePath = Field(repr=False)
    # Addresses and CIDR blocks, comma-separated, of the proxies allowed to name the client in X-Forwarded-For.
    trusted_proxies: str = ""
    # May carry a password, so it never shows in a repr.
    redis_url: RedisDsn = Field(repr=False)
    turn_provider: TurnProvider = TurnProvider.OFF
    turn_key_id_file: AbsolutePath | None = Field(default=None, repr=False)
    turn_api_token_file: AbsolutePath | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def _require_the_turn_key_files_when_turn_is_on(self) -> Self:
        if self.turn_provider is TurnProvider.OFF:
            return self
        prefix = self.model_config["env_prefix"]
        missing = [
            f"{prefix}{setting}".upper()
            for setting, path in [
                ("turn_key_id_file", self.turn_key_id_file),
                ("turn_api_token_file", self.turn_api_token_file),
            ]
            if path is None
        ]
        if missing:
            raise ValueError(f"{' and '.join(missing)} must be set when {prefix}TURN_PROVIDER is {self.turn_provider}")
        return self
