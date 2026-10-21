import secrets
from dataclasses import dataclass, field
from pathlib import Path

from hopin.config.settings import ServerSettings

TEST_ISSUER = "https://issuer.test.invalid/realms/test"
TEST_PUBLIC_ORIGIN = "https://hopin.test.invalid"
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
    values: dict[str, str | int | Path | None] = {
        "oidc_issuer": TEST_ISSUER,
        "oidc_client_secret_file": client_secret_file,
        "public_origin": TEST_PUBLIC_ORIGIN,
        "session_secret_file": session_secret_file,
        **overrides,
    }
    present = {name: value for name, value in values.items() if value is not None}
    return SignInSettings(
        environment={f"HOPIN_{name.upper()}": str(value) for name, value in present.items()},
        settings=ServerSettings(**present),
        client_secret=client_secret,
        session_secret=session_secret,
    )
