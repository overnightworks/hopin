import time
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

import structlog
from fastapi import APIRouter, Request, Response
from webauth.admission import AdmissionConfig
from webauth.config import SessionLifetime, WebAuthConfig
from webauth.https_client import fetch_key_set_over_https
from webauth.oidc import OidcClientConfig, TokenRequestPost
from webauth.oidc_transport import post_token_request_over_https
from webauth.policies import CsrfPolicy, RateLimitPolicy
from webauth.proxies import TrustedProxies
from webauth.sign_in_routes import LogoutPageText, RefusalPage, SignInRefusal
from webauth.web_auth import IssuerCalls, SignInPages, WebAuth, WebAuthSettings, WebAuthStores

from hopin.config.constants import (
    AUTH_CALLBACK_PATH,
    LOGIN_PATH,
    NO_ACCESS_PATH,
    OIDC_LEEWAY_SECONDS,
    OIDC_SCOPES,
    READ_METHODS,
    SESSION_LIFETIME_SECONDS,
)
from hopin.config.secret_files import StartRefusedError, read_secret_file
from hopin.config.web_policy import RATE_LIMIT_EXEMPT_PATH_RULES, SIGN_IN_PATHS
from hopin.web.page_build import built_page_response
from hopin.words import text

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import SecretStr
    from webauth.jwt_verification import KeySetFetch
    from webauth.middleware import SessionGate
    from webauth.policies import SecurityHeadersPolicy
    from webauth.ports import AccountEvent, RateLimitBackend

    from hopin.config.settings import ServerSettings
    from hopin.web.page_build import PageBuild
    from hopin.words import Language

OPENID_CONNECT_PATH = "/protocol/openid-connect"

logger = structlog.get_logger()


class LoggingAuditSink:
    """Logs each account event by its kind, the issuer's subject and the role, never a username."""

    def account_event(self, event: AccountEvent) -> None:
        logger.info("account.changed", kind=event.kind.value, sub=event.subject_id, role=event.role)


class SignInConfigurationError(StartRefusedError):
    """The sign-in settings cannot start HopIn; the message names settings, never their values."""


@dataclass(frozen=True)
class SignInConfigs:
    oidc_client: OidcClientConfig
    web_auth: WebAuthConfig
    trusted_proxies: TrustedProxies
    groups: AdmissionConfig


@dataclass(frozen=True)
class IssuerTransports:
    """The two network calls to the issuer, the only seam a test replaces."""

    post_token_request: TokenRequestPost
    fetch_key_set: KeySetFetch


ISSUER_OVER_HTTPS = IssuerTransports(
    post_token_request=post_token_request_over_https, fetch_key_set=fetch_key_set_over_https
)


def build_sign_in_configs(
    settings: ServerSettings,
    feature_scopes: tuple[str, ...],
    rate_limits: RateLimitBackend,
    wall_clock: Callable[[], float] = time.time,
) -> SignInConfigs:
    client_secret = read_secret_file(settings.oidc_client_secret_file, "HOPIN_OIDC_CLIENT_SECRET_FILE")
    session_secret = read_secret_file(settings.session_secret_file, "HOPIN_SESSION_SECRET_FILE")
    try:
        oidc_client = _build_oidc_client(settings, client_secret, feature_scopes)
    except ValueError as refusal:
        raise SignInConfigurationError(f"HOPIN_OIDC_ISSUER and HOPIN_PUBLIC_ORIGIN are refused: {refusal}") from None
    trusted_proxies = _parse_trusted_proxies(settings.trusted_proxies)
    try:
        web_auth = _build_web_auth(settings, session_secret, rate_limits, wall_clock)
    except ValueError as refusal:
        raise SignInConfigurationError(f"HOPIN_SESSION_SECRET_FILE is refused: {refusal}") from None
    groups = AdmissionConfig(
        bound_issuer=oidc_client.issuer, login_group=settings.realm.login_group, admin_group=settings.realm.admin_group
    )
    return SignInConfigs(oidc_client=oidc_client, web_auth=web_auth, trusted_proxies=trusted_proxies, groups=groups)


def _parse_trusted_proxies(raw: str) -> TrustedProxies:
    try:
        return TrustedProxies.parse(raw)
    except ValueError:
        raise SignInConfigurationError("HOPIN_TRUSTED_PROXIES is refused: name addresses or CIDR blocks") from None


def _build_oidc_client(
    settings: ServerSettings, client_secret: SecretStr, feature_scopes: tuple[str, ...]
) -> OidcClientConfig:
    openid_connect = settings.oidc_issuer + OPENID_CONNECT_PATH
    return OidcClientConfig(
        issuer=settings.oidc_issuer,
        authorization_endpoint=f"{openid_connect}/auth",
        token_endpoint=f"{openid_connect}/token",
        jwks_url=f"{openid_connect}/certs",
        end_session_endpoint=f"{openid_connect}/logout",
        client_id=settings.realm.client_id,
        client_secret=client_secret,
        redirect_uri=settings.public_origin + AUTH_CALLBACK_PATH,
        post_logout_redirect_uri=settings.public_origin + LOGIN_PATH,
        scopes=(*OIDC_SCOPES, *feature_scopes),
        leeway_seconds=OIDC_LEEWAY_SECONDS,
    )


def _build_web_auth(
    settings: ServerSettings,
    session_secret: SecretStr,
    rate_limits: RateLimitBackend,
    wall_clock: Callable[[], float],
) -> WebAuthConfig:
    return WebAuthConfig(
        session_secret=session_secret,
        rate_limits=rate_limits,
        wall_clock=wall_clock,
        allowed_hosts_exact=frozenset({urlsplit(settings.public_origin).netloc.lower()}),
        session_lifetime=SessionLifetime(
            idle_seconds=SESSION_LIFETIME_SECONDS, absolute_max_age_seconds=SESSION_LIFETIME_SECONDS
        ),
        cookie_samesite="lax",
        cookie_secure=True,
    )


def words_logout_button_label(language: str) -> str:
    # The offered languages are the words' own, so the resolved one is a words language.
    return text(cast("Language", language), "signIn.logOutButton")


def build_web_auth(
    sign_in_configs: SignInConfigs,
    stores: WebAuthStores,
    page_build: PageBuild,
    request_language: Callable[[Request], str],
    security_headers: SecurityHeadersPolicy,
    issuer_transports: IssuerTransports,
    monotonic_clock: Callable[[], float] = time.monotonic,
    gate: SessionGate | None = None,
    logout_button_label: Callable[[str], str] = words_logout_button_label,
) -> WebAuth:
    """HopIn's whole auth, built before its routes: the library serves the sign-in routes, the pages stay HopIn's."""
    client = sign_in_configs.oidc_client
    return WebAuth.build(
        WebAuthSettings(
            config=sign_in_configs.web_auth,
            client=client,
            sign_in_paths=SIGN_IN_PATHS,
            keeps_id_token=True,
            max_sessions=None,
            security_headers=security_headers,
            csrf_origin=CsrfPolicy(),
            rate_limit=RateLimitPolicy(exempt=RATE_LIMIT_EXEMPT_PATH_RULES),
        ),
        stores=stores,
        groups=sign_in_configs.groups,
        pages=SignInPages(
            refused=lambda request, refusal: _refusal_response(refusal, page_build, request_language(request)),
            logout_page_text=lambda request: _logout_page_text(request_language(request), logout_button_label),
            ui_language=request_language,
        ),
        front=sign_in_configs.trusted_proxies,
        gate=gate,
        issuer_calls=IssuerCalls(
            post_token_request=issuer_transports.post_token_request,
            fetch_key_set=issuer_transports.fetch_key_set,
            monotonic_clock=monotonic_clock,
        ),
    )


def _logout_page_text(language: str, button_label_of: Callable[[str], str]) -> LogoutPageText:
    return LogoutPageText(language=language, button_label=button_label_of(language))


def create_pages_router(page_build: PageBuild, request_language: Callable[[Request], str]) -> APIRouter:
    router = APIRouter()

    @router.api_route(LOGIN_PATH, methods=READ_METHODS)
    def signed_out(request: Request) -> Response:
        return built_page_response(page_build.signed_out, request_language(request))

    @router.api_route(NO_ACCESS_PATH, methods=READ_METHODS)
    def no_access(request: Request) -> Response:
        return built_page_response(page_build.no_access, request_language(request))

    return router


def _refusal_response(refusal: SignInRefusal, page_build: PageBuild, language: str) -> Response:
    if refusal.page is RefusalPage.NO_ACCESS:
        return Response(status_code=HTTPStatus.SEE_OTHER, headers={"location": NO_ACCESS_PATH})
    return built_page_response(page_build.login_failed, language, status_code=HTTPStatus.FORBIDDEN)
