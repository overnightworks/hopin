from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import SecretStr
from webauth.config import SessionLifetime, WebAuthConfig
from webauth.oidc import OidcClientConfig
from webauth.proxies import TrustedProxies
from webauth.rate_limit import SingleProcessRateLimitBackend

from hopin.config.constants import (
    AUTH_CALLBACK_PATH,
    LOGIN_PATH,
    LOGOUT_PATH,
    OIDC_LEEWAY_SECONDS,
    OIDC_SCOPES,
    SESSION_LIFETIME_SECONDS,
)
from hopin.config.settings import ServerSettings

OPENID_CONNECT_PATH = "/protocol/openid-connect"


class SignInConfigurationError(Exception):
    """The sign-in settings cannot start HopIn; the message names settings, never their values."""


@dataclass(frozen=True)
class SignInConfigs:
    oidc_client: OidcClientConfig
    web_auth: WebAuthConfig


def build_sign_in_configs(settings: ServerSettings) -> SignInConfigs:
    client_secret = _read_secret_file(settings.oidc_client_secret_file, "oidc_client_secret_file")
    session_secret = _read_secret_file(settings.session_secret_file, "session_secret_file")
    try:
        oidc_client = _build_oidc_client(settings, client_secret)
    except ValueError as refusal:
        raise SignInConfigurationError(f"oidc_issuer and public_origin are refused: {refusal}") from None
    try:
        web_auth = _build_web_auth(settings, session_secret)
    except ValueError as refusal:
        raise SignInConfigurationError(f"session_secret_file is refused: {refusal}") from None
    return SignInConfigs(oidc_client=oidc_client, web_auth=web_auth)


def _read_secret_file(path: Path, setting: str) -> SecretStr:
    try:
        content = path.read_bytes().decode("utf-8").removesuffix("\n")
    except (OSError, UnicodeDecodeError):
        raise SignInConfigurationError(f"{setting} names a file that cannot be read") from None
    if not content:
        raise SignInConfigurationError(f"{setting} names an empty file") from None
    return SecretStr(content)


def _build_oidc_client(settings: ServerSettings, client_secret: SecretStr) -> OidcClientConfig:
    openid_connect = settings.oidc_issuer + OPENID_CONNECT_PATH
    return OidcClientConfig(
        issuer=settings.oidc_issuer,
        authorization_endpoint=f"{openid_connect}/auth",
        token_endpoint=f"{openid_connect}/token",
        jwks_url=f"{openid_connect}/certs",
        end_session_endpoint=f"{openid_connect}/logout",
        client_id=settings.oidc_client_id,
        client_secret=client_secret,
        redirect_uri=settings.public_origin + AUTH_CALLBACK_PATH,
        post_logout_redirect_uri=settings.public_origin + LOGIN_PATH,
        logout_path=LOGOUT_PATH,
        scopes=OIDC_SCOPES,
        leeway_seconds=OIDC_LEEWAY_SECONDS,
    )


def _build_web_auth(settings: ServerSettings, session_secret: SecretStr) -> WebAuthConfig:
    return WebAuthConfig(
        session_secret=session_secret,
        trusted_proxies=TrustedProxies(),
        rate_limits=SingleProcessRateLimitBackend(),
        allowed_hosts_exact=frozenset({urlsplit(settings.public_origin).netloc.lower()}),
        allowed_hosts_patterns=(),
        session_lifetime=SessionLifetime(
            idle_seconds=SESSION_LIFETIME_SECONDS, absolute_max_age_seconds=SESSION_LIFETIME_SECONDS
        ),
        cookie_samesite="lax",
        cookie_secure=True,
    )
