import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from hopin.config.settings import RealmSettings, ServerSettings

if TYPE_CHECKING:
    from pathlib import Path

TEST_ISSUER = "https://issuer.test.invalid/realms/test"
END_SESSION_URL = f"{TEST_ISSUER}/protocol/openid-connect/logout"
TEST_PUBLIC_ORIGIN = "https://hopin.test.invalid"
TEST_REDIS_URL = "redis://redis.test.invalid:6379"
TEST_DATABASE_URL = "postgresql://hopin@postgres.test.invalid:5432/hopin"
TEST_REALM = RealmSettings(
    client_id="test-client",
    login_group="/apps/test",
    admin_group="/apps/test/admin",
    mcp_group="/apps/test/mcp",
    mcp_scope="mcp-test",
    agent_group="/agents-test",
    mcp_client_id="agents-test",
)
SESSION_SECRET_BYTES = 36
CLIENT_SECRET_BYTES = 24


@dataclass(frozen=True)
class SignInSettings:
    environment: dict[str, str] = field(repr=False)
    settings: ServerSettings
    client_secret: str = field(repr=False)
    session_secret: str = field(repr=False)


def build_sign_in_settings(directory: Path, **overrides: str | int | Path | None) -> SignInSettings:
    """Write throwaway secret files into `directory`; an override of None leaves that setting out."""
    client_secret = secrets.token_urlsafe(CLIENT_SECRET_BYTES)
    session_secret = secrets.token_urlsafe(SESSION_SECRET_BYTES)
    client_secret_file = directory / "client-secret"
    session_secret_file = directory / "session-secret"
    client_secret_file.write_text(client_secret + "\n")
    session_secret_file.write_text(session_secret + "\n")
    database_password_file = directory / "database-password"
    database_password_file.write_text(secrets.token_urlsafe(CLIENT_SECRET_BYTES) + "\n")
    values: dict[str, str | int | Path | None] = {
        "oidc_issuer": TEST_ISSUER,
        "oidc_client_secret_file": client_secret_file,
        "public_origin": TEST_PUBLIC_ORIGIN,
        "session_secret_file": session_secret_file,
        "redis_url": TEST_REDIS_URL,
        "database_url": TEST_DATABASE_URL,
        "database_password_file": database_password_file,
        **overrides,
    }
    present = {name: value for name, value in values.items() if value is not None}
    return SignInSettings(
        environment={
            **{f"HOPIN_{name.upper()}": str(value) for name, value in present.items()},
            **{f"REALM_{name.upper()}": value for name, value in TEST_REALM.model_dump().items()},
        },
        settings=ServerSettings(**present),
        client_secret=client_secret,
        session_secret=session_secret,
    )
