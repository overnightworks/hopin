import asyncio
from contextlib import nullcontext
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, assert_never

import structlog
from webauth.dependencies import session_id_from_request
from webauth.oidc import OidcRefusal
from webauth.oidc_login import AccessTokenRefusal, AccessTokenRefusalReason, fresh_access_token

from hopin.config.constants import GameRefusal

if TYPE_CHECKING:
    from pydantic import SecretStr
    from starlette.requests import HTTPConnection
    from webauth.config import WebAuthConfig
    from webauth.oidc import OidcRelyingParty
    from webauth.ports import SessionRecordStore

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class RunToken(StrEnum):
    PERSON = "person"
    WORKLOAD = "workload"


@dataclass(frozen=True)
class RunTokens:
    """The starter's access token and HopIn's workload token, held for one run only."""

    person: SecretStr = field(repr=False)
    workload: SecretStr = field(repr=False)


class StarterTokens:
    """Fetches the two tokens a run is sent with, from the issuer, for the session the starter's socket opened with."""

    def __init__(self, relying_party: OidcRelyingParty, sessions: SessionRecordStore, web_auth: WebAuthConfig) -> None:
        self._relying_party = relying_party
        self._sessions = sessions
        self._web_auth = web_auth

    async def of(self, starter: HTTPConnection) -> RunTokens | GameRefusal:
        # Both grants block on the issuer, so each runs in a worker. A cancelled run cancels the await,
        # not the worker: its token is dropped unread and no later step runs.
        session_id = session_id_from_request(starter, self._web_auth)
        if session_id is None:
            return GameRefusal.SIGN_IN_AGAIN
        person = await asyncio.to_thread(self._person_token, session_id)
        if isinstance(person, AccessTokenRefusal):
            return _refusal_of_person_token(person)
        workload = await asyncio.to_thread(self._relying_party.workload_access_token)
        if isinstance(workload, OidcRefusal):
            _log_refusal(RunToken.WORKLOAD, workload)
            return GameRefusal.UNAVAILABLE
        return RunTokens(person=person, workload=workload)

    def _person_token(self, session_id: str) -> SecretStr | AccessTokenRefusal:
        return fresh_access_token(
            self._relying_party,
            session_id,
            session_transaction=lambda: nullcontext(self._sessions),
            config=self._web_auth,
        )


def _refusal_of_person_token(refusal: AccessTokenRefusal) -> GameRefusal:
    _log_refusal(RunToken.PERSON, refusal.issuer_refusal, reason=refusal.reason)
    match refusal.reason:
        case AccessTokenRefusalReason.SESSION_ENDED | AccessTokenRefusalReason.SIGN_IN_AGAIN:
            return GameRefusal.SIGN_IN_AGAIN
        case AccessTokenRefusalReason.ISSUER_UNAVAILABLE:
            return GameRefusal.UNAVAILABLE
        case _:  # pragma: no cover - the type checker proves the match exhaustive
            assert_never(refusal.reason)


def _log_refusal(
    token: RunToken, issuer_refusal: OidcRefusal | None, *, reason: AccessTokenRefusalReason | None = None
) -> None:
    codes = {
        "reason": None if reason is None else reason.value,
        "issuer_reason": None if issuer_refusal is None else issuer_refusal.reason.value,
        "idp_error": None if issuer_refusal is None else issuer_refusal.idp_error,
    }
    logger.info(
        "game_master_token_refused",
        token=token.value,
        **{name: code for name, code in codes.items() if code},
    )
