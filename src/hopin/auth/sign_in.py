import time
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import structlog
from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from webauth.config import SessionLifetime, WebAuthConfig, web_auth_config
from webauth.cookies import issue_session_cookies
from webauth.https_client import fetch_key_set_over_https
from webauth.oidc import OidcClientConfig, OidcRefusal, OidcRelyingParty, TokenRequestPost
from webauth.oidc_login import (
    AdmissionConfig,
    AdmissionRefusal,
    AdmittedLogin,
    BackchannelLogoutResult,
    finish_login,
    log_out,
    open_session,
    receive_backchannel_logout,
    start_login,
)
from webauth.oidc_transport import post_token_request_over_https
from webauth.proxies import TrustedProxies
from webauth.rate_limit import SingleProcessRateLimitBackend

from hopin.config.constants import (
    ADMIN_GROUP,
    AUTH_CALLBACK_PATH,
    AUTH_START_PATH,
    BACKCHANNEL_LOGOUT_PATH,
    HOME_PATH,
    LOGIN_GROUP,
    LOGIN_PATH,
    LOGOUT_PATH,
    NO_ACCESS_PATH,
    OIDC_LEEWAY_SECONDS,
    OIDC_SCOPES,
    RETURN_TO_QUERY_PARAMETER,
    SESSION_LIFETIME_SECONDS,
)
from hopin.config.secret_files import StartRefusedError, read_secret_file
from hopin.web.routes import READ_METHODS, STATIC_DIR

if TYPE_CHECKING:
    from pydantic import SecretStr
    from webauth.jwt_verification import KeySetFetch
    from webauth.ports import AuditSink, SessionRecord, SessionRecordStore, SubjectUserStore, WriteLock

    from hopin.config.settings import ServerSettings

OPENID_CONNECT_PATH = "/protocol/openid-connect"
LOGOUT_LABEL = "Log out"
LOGIN_FAILED_PAGE = STATIC_DIR / "login-failed.html"
SIGNED_OUT_PAGE = STATIC_DIR / "login.html"
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


def build_sign_in_configs(settings: ServerSettings) -> SignInConfigs:
    client_secret = read_secret_file(settings.oidc_client_secret_file, "oidc_client_secret_file")
    session_secret = read_secret_file(settings.session_secret_file, "session_secret_file")
    try:
        oidc_client = _build_oidc_client(settings, client_secret)
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
        scopes=OIDC_SCOPES,
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


def create_sign_in_router(
    relying_party: OidcRelyingParty,
    *,
    accounts: SubjectUserStore,
    sessions: SessionRecordStore,
    lock: WriteLock,
    audit: AuditSink,
) -> APIRouter:
    admission = AdmissionConfig(bound_issuer=relying_party.issuer, login_group=LOGIN_GROUP, admin_group=ADMIN_GROUP)
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
        session = _open_session(request, config, outcome, sessions, lock)
        issue_session_cookies(answer, session.id, config)
        answer.status_code = HTTPStatus.SEE_OTHER
        answer.headers["location"] = outcome.return_to or HOME_PATH
        return answer

    @router.post(LOGOUT_PATH)
    def logout(request: Request) -> Response:
        return log_out(
            relying_party, request, web_auth_config(request), sessions=sessions, lock=lock, button_label=LOGOUT_LABEL
        )

    @router.post(BACKCHANNEL_LOGOUT_PATH)
    async def backchannel_logout(request: Request) -> Response:
        # No catch-all: a failure before the lock ends nothing and answers 500, and one after it
        # (the audit) answers 500 with the sessions already ended, which the in-memory stores cannot roll back.
        result = await run_in_threadpool(
            receive_backchannel_logout,
            relying_party,
            await request.body(),
            request.headers.get("content-type"),
            accounts=accounts,
            sessions=sessions,
            lock=lock,
            audit=audit,
        )
        accepted = result is BackchannelLogoutResult.ACCEPTED
        return Response(status_code=HTTPStatus.OK if accepted else HTTPStatus.BAD_REQUEST)

    @router.api_route(LOGIN_PATH, methods=READ_METHODS)
    def signed_out() -> FileResponse:
        return FileResponse(SIGNED_OUT_PAGE)

    @router.api_route(NO_ACCESS_PATH, methods=READ_METHODS)
    def no_access() -> FileResponse:
        return FileResponse(NO_ACCESS_PAGE)

    return router


def _open_session(
    request: Request, config: WebAuthConfig, outcome: AdmittedLogin, sessions: SessionRecordStore, lock: WriteLock
) -> SessionRecord:
    """HopIn logs out through `log_out`, so its session rows keep the ID token."""
    return open_session(request, config, outcome, sessions=sessions, lock=lock, max_sessions=None, keeps_id_token=True)


def _refusal_response(refusal: OidcRefusal | AdmissionRefusal, *, carrying: Response) -> Response:
    _log_refusal(refusal)
    if refusal is AdmissionRefusal.NOT_A_MEMBER:
        response: Response = Response(status_code=HTTPStatus.SEE_OTHER, headers={"location": NO_ACCESS_PATH})
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
