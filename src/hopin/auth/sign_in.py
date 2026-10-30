import time
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import structlog
from fastapi import APIRouter, FastAPI, Response
from fastapi.responses import FileResponse
from webauth.config import SessionLifetime, WebAuthConfig
from webauth.https_client import fetch_key_set_over_https
from webauth.oidc import OidcClientConfig, OidcRefusal, OidcRelyingParty, TokenRequestPost
from webauth.oidc_login import AdmissionConfig, AdmissionRefusal
from webauth.oidc_transport import post_token_request_over_https
from webauth.proxies import TrustedProxies
from webauth.rate_limit import SingleProcessRateLimitBackend
from webauth.sign_in_routes import RefusalPage, SignInRefusal, install_sign_in_routes

from hopin.config.constants import (
    ADMIN_GROUP,
    AUTH_CALLBACK_PATH,
    LOGIN_GROUP,
    LOGIN_PATH,
    NO_ACCESS_PATH,
    OIDC_LEEWAY_SECONDS,
    OIDC_SCOPES,
    SESSION_LIFETIME_SECONDS,
    SIGN_IN_PATHS,
)
from hopin.config.secret_files import StartRefusedError, read_secret_file
from hopin.web.routes import READ_METHODS, STATIC_DIR, built_page_response

if TYPE_CHECKING:
    from pydantic import SecretStr
    from webauth.jwt_verification import KeySetFetch
    from webauth.ports import SignInTransaction

    from hopin.config.settings import ServerSettings
    from hopin.web.page_build import PageBuild

OPENID_CONNECT_PATH = "/protocol/openid-connect"
LOGOUT_LABEL = "Log out"
NO_ACCESS_PAGE = STATIC_DIR / "no-access.html"

logger = structlog.get_logger()


class SignInConfigurationError(StartRefusedError):
    """The sign-in settings cannot start HopIn; the message names settings, never their values."""


@dataclass(frozen=True)
class SignInConfigs:
    oidc_client: OidcClientConfig
    web_auth: WebAuthConfig


@dataclass(frozen=True)
class IssuerTransports:
    """The two network calls to the issuer, the only seam a test replaces."""

    post_token_request: TokenRequestPost
    fetch_key_set: KeySetFetch


ISSUER_OVER_HTTPS = IssuerTransports(
    post_token_request=post_token_request_over_https, fetch_key_set=fetch_key_set_over_https
)


def build_sign_in_configs(settings: ServerSettings, feature_scopes: tuple[str, ...]) -> SignInConfigs:
    client_secret = read_secret_file(settings.oidc_client_secret_file, "oidc_client_secret_file")
    session_secret = read_secret_file(settings.session_secret_file, "session_secret_file")
    try:
        oidc_client = _build_oidc_client(settings, client_secret, feature_scopes)
    except ValueError as refusal:
        raise SignInConfigurationError(f"oidc_issuer and public_origin are refused: {refusal}") from None
    trusted_proxies = _parse_trusted_proxies(settings.trusted_proxies)
    try:
        web_auth = _build_web_auth(settings, session_secret, trusted_proxies)
    except ValueError as refusal:
        raise SignInConfigurationError(f"session_secret_file is refused: {refusal}") from None
    return SignInConfigs(oidc_client=oidc_client, web_auth=web_auth)


def _parse_trusted_proxies(raw: str) -> TrustedProxies:
    try:
        return TrustedProxies.parse(raw)
    except ValueError:
        raise SignInConfigurationError("trusted_proxies is refused: name addresses or CIDR blocks") from None


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
        client_id=settings.oidc_client_id,
        client_secret=client_secret,
        redirect_uri=settings.public_origin + AUTH_CALLBACK_PATH,
        post_logout_redirect_uri=settings.public_origin + LOGIN_PATH,
        scopes=(*OIDC_SCOPES, *feature_scopes),
        leeway_seconds=OIDC_LEEWAY_SECONDS,
    )


def _build_web_auth(
    settings: ServerSettings, session_secret: SecretStr, trusted_proxies: TrustedProxies
) -> WebAuthConfig:
    return WebAuthConfig(
        session_secret=session_secret,
        trusted_proxies=trusted_proxies,
        rate_limits=SingleProcessRateLimitBackend(),
        allowed_hosts_exact=frozenset({urlsplit(settings.public_origin).netloc.lower()}),
        session_lifetime=SessionLifetime(
            idle_seconds=SESSION_LIFETIME_SECONDS, absolute_max_age_seconds=SESSION_LIFETIME_SECONDS
        ),
        cookie_samesite="lax",
        cookie_secure=True,
    )


def build_relying_party(sign_in_configs: SignInConfigs, issuer_transports: IssuerTransports) -> OidcRelyingParty:
    return OidcRelyingParty(
        sign_in_configs.oidc_client,
        post_token_request=issuer_transports.post_token_request,
        fetch_key_set=issuer_transports.fetch_key_set,
        wall_clock=sign_in_configs.web_auth.wall_clock,
        monotonic_clock=time.monotonic,
    )


def install_sign_in(
    app: FastAPI, relying_party: OidcRelyingParty, transaction: SignInTransaction, page_build: PageBuild
) -> None:
    """The library serves the start, callback, logout and back-channel routes; the pages stay HopIn's."""
    install_sign_in_routes(
        app,
        paths=SIGN_IN_PATHS,
        relying_party=lambda _request: relying_party,
        admission=AdmissionConfig(bound_issuer=relying_party.issuer, login_group=LOGIN_GROUP, admin_group=ADMIN_GROUP),
        transaction=transaction,
        refused=lambda _request, refusal: _refusal_response(refusal, page_build),
        keeps_id_token=True,
        max_sessions=None,
        logout_button_label=lambda _request: LOGOUT_LABEL,
    )
    app.include_router(_create_pages_router(page_build))


def _create_pages_router(page_build: PageBuild) -> APIRouter:
    router = APIRouter()

    @router.api_route(LOGIN_PATH, methods=READ_METHODS)
    def signed_out() -> Response:
        return built_page_response(page_build.signed_out)

    @router.api_route(NO_ACCESS_PATH, methods=READ_METHODS)
    def no_access() -> FileResponse:
        return FileResponse(NO_ACCESS_PAGE)

    return router


def _refusal_response(refusal: SignInRefusal, page_build: PageBuild) -> Response:
    _log_refusal(refusal.cause)
    if refusal.page is RefusalPage.NO_ACCESS:
        return Response(status_code=HTTPStatus.SEE_OTHER, headers={"location": NO_ACCESS_PATH})
    return built_page_response(page_build.login_failed, status_code=HTTPStatus.FORBIDDEN)


def _log_refusal(refusal: OidcRefusal | AdmissionRefusal) -> None:
    if isinstance(refusal, AdmissionRefusal):
        logger.info("sign_in_refused", reason=refusal.value)
        return
    codes = {
        "idp_error": refusal.idp_error,
        "token_rejection": None if refusal.token_rejection is None else refusal.token_rejection.value,
    }
    logger.info("sign_in_refused", reason=refusal.reason.value, **{name: code for name, code in codes.items() if code})
