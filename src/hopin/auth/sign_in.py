import time
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import structlog
from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import SecretStr
from webauth.config import SessionLifetime, WebAuthConfig, web_auth_config
from webauth.cookies import issue_session_cookies
from webauth.https_client import fetch_key_set_over_https
from webauth.oidc import OidcClientConfig, OidcRefusal, OidcRelyingParty, TokenRequestPost
from webauth.oidc_login import (
    AdmissionConfig,
    AdmissionRefusal,
    AdmittedLogin,
    NoAdmins,
    finish_login,
    open_session,
    start_login,
)
from webauth.oidc_transport import post_token_request_over_https
from webauth.proxies import TrustedProxies
from webauth.rate_limit import SingleProcessRateLimitBackend

from hopin.config.constants import (
    AUTH_CALLBACK_PATH,
    AUTH_START_PATH,
    HOME_PATH,
    LOGIN_GROUP,
    LOGIN_PATH,
    LOGOUT_PATH,
    OIDC_LEEWAY_SECONDS,
    OIDC_SCOPES,
    RETURN_TO_QUERY_PARAMETER,
    SESSION_LIFETIME_SECONDS,
)
from hopin.web.routes import STATIC_DIR

if TYPE_CHECKING:
    from pathlib import Path

    from webauth.jwt_verification import KeySetFetch
    from webauth.ports import AuditSink, SessionRecordStore, SubjectUserStore, WriteLock

    from hopin.config.settings import ServerSettings

OPENID_CONNECT_PATH = "/protocol/openid-connect"
NO_ACCESS_BODY = "No access"
LOGIN_FAILED_PAGE = STATIC_DIR / "login-failed.html"

logger = structlog.get_logger()


class SignInConfigurationError(Exception):
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
    except OSError, UnicodeDecodeError:
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


def create_sign_in_router(
    oidc_client: OidcClientConfig,
    web_auth: WebAuthConfig,
    issuer_transports: IssuerTransports,
    *,
    accounts: SubjectUserStore,
    sessions: SessionRecordStore,
    lock: WriteLock,
    audit: AuditSink,
) -> APIRouter:
    relying_party = OidcRelyingParty(
        oidc_client,
        post_token_request=issuer_transports.post_token_request,
        fetch_key_set=issuer_transports.fetch_key_set,
        wall_clock=web_auth.wall_clock,
        monotonic_clock=time.monotonic,
    )
    admission = AdmissionConfig(
        bound_issuer=oidc_client.issuer, login_group=LOGIN_GROUP, admin_group=NoAdmins.IN_THIS_APP
    )
    router = APIRouter()

    @router.get(AUTH_START_PATH)
    def start(request: Request) -> Response:
        answer = Response(status_code=HTTPStatus.SEE_OTHER)
        answer.headers["location"] = start_login(
            relying_party,
            request,
            answer,
            web_auth_config(request),
            return_to=request.query_params.get(RETURN_TO_QUERY_PARAMETER),
        )
        return answer

    @router.get(AUTH_CALLBACK_PATH)
    def callback(request: Request) -> Response:
        config = web_auth_config(request)
        answer = Response()
        outcome = finish_login(
            relying_party,
            request,
            answer,
            config,
            admission=admission,
            accounts=accounts,
            lock=lock,
            audit=audit,
        )
        if not isinstance(outcome, AdmittedLogin):
            return _refusal_response(outcome, carrying=answer)
        session = open_session(request, config, outcome, sessions=sessions, lock=lock, max_sessions=None)
        issue_session_cookies(answer, session.id, config)
        answer.status_code = HTTPStatus.SEE_OTHER
        answer.headers["location"] = outcome.return_to or HOME_PATH
        return answer

    return router


def _refusal_response(refusal: OidcRefusal | AdmissionRefusal, *, carrying: Response) -> Response:
    _log_refusal(refusal)
    if refusal is AdmissionRefusal.NOT_A_MEMBER:
        response: Response = PlainTextResponse(NO_ACCESS_BODY, status_code=HTTPStatus.FORBIDDEN)
    else:
        response = FileResponse(LOGIN_FAILED_PAGE, status_code=HTTPStatus.FORBIDDEN)
    for cookie in carrying.headers.getlist("set-cookie"):
        response.headers.append("set-cookie", cookie)
    return response


def _log_refusal(refusal: OidcRefusal | AdmissionRefusal) -> None:
    if isinstance(refusal, AdmissionRefusal):
        logger.info("sign_in_refused", reason=refusal.value)
        return
    codes = {
        "idp_error": refusal.idp_error,
        "token_rejection": None if refusal.token_rejection is None else refusal.token_rejection.value,
    }
    logger.info("sign_in_refused", reason=refusal.reason.value, **{name: code for name, code in codes.items() if code})
