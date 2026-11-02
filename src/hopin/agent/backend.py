import asyncio
import logging
from contextlib import aclosing, nullcontext
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Final, assert_never

import structlog
from agent_runtime_client.agents import load_agent
from agent_runtime_client.backend import AgentBackend, Asker, RunTokens, TurnStopKind, TurnStopped
from agent_runtime_client.backend.service import ServiceBackend
from agent_runtime_client.capability import CapabilityPrincipal
from agent_runtime_client.turn_events import AssistantTextEvent, FinalEvent, ToolCallEvent, ToolResultEvent
from agent_runtime_client.wire import RejectionCode, TurnMessage, TurnRole
from webauth.dependencies import session_id_from_request
from webauth.oidc import OidcRefusal
from webauth.oidc_login import AccessTokenRefusal, AccessTokenRefusalReason, fresh_access_token

from hopin.agent.tools import TOOLS
from hopin.game.quiz import Speaker, Turn
from hopin.game.table import GameOutcome, Line, Refused
from hopin.game.vocabulary import (
    GAME_LINE_MAX_CHARACTERS,
    GAME_RETRY_AFTER_FALLBACK_SECONDS,
    GameRefusal,
)

if TYPE_CHECKING:
    from pydantic import SecretStr
    from starlette.requests import HTTPConnection
    from webauth.config import WebAuthConfig
    from webauth.oidc import OidcRelyingParty
    from webauth.ports import SessionRecordStore

    from hopin.agent.settings import AgentSettings

type QuizBackend = AgentBackend

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

QUIZ_AGENT_FILE: Final = Path(__file__).parent.parent / "agents" / "quiz.md"
CUT_MARK: Final = "…"
UNAVAILABLE: Final = Refused(GameRefusal.UNAVAILABLE)
ROLE_OF_SPEAKER: Final = {Speaker.HOPIN: TurnRole.USER, Speaker.GAME_MASTER: TurnRole.ASSISTANT}
NO_ACCESS_CODES: Final = frozenset({RejectionCode.NOT_ENABLED, RejectionCode.FORBIDDEN})
# The loggers httpx2 and httpcore2 log on: the status line at INFO, the headers and every transport error
# at DEBUG, where the agent service may echo a token or the game's text. A logger's filter sees only the
# records logged on that logger, not on its children, so each one gets it; only this package sends through them.
TRANSPORT_LOGGERS: Final = (
    "httpx2",
    "httpcore2.connection",
    "httpcore2.http11",
    "httpcore2.http2",
    "httpcore2.proxy",
    "httpcore2.socks",
)


def _unlogged(record: logging.LogRecord) -> bool:
    del record
    return False


def silence_transport_logs() -> None:
    for logger_name in TRANSPORT_LOGGERS:
        logging.getLogger(logger_name).addFilter(_unlogged)


def service_backend(settings: AgentSettings) -> QuizBackend:
    """The client's backend on the agent service; as a side effect, silence the transport loggers
    that could write a token."""
    silence_transport_logs()
    return ServiceBackend(settings.agent)


class RunToken(StrEnum):
    PERSON = "person"
    WORKLOAD = "workload"


class IssuerError(StrEnum):
    """The token endpoint's error codes of RFC 6749 section 5.2; any other code the issuer names is `other`."""

    INVALID_REQUEST = "invalid_request"
    INVALID_CLIENT = "invalid_client"
    INVALID_GRANT = "invalid_grant"
    UNAUTHORIZED_CLIENT = "unauthorized_client"
    UNSUPPORTED_GRANT_TYPE = "unsupported_grant_type"
    INVALID_SCOPE = "invalid_scope"
    OTHER = "other"

    @classmethod
    def of(cls, idp_error: str) -> IssuerError:
        # Only this closed vocabulary reaches a log: the issuer's own text may be shaped like a credential.
        try:
            return cls(idp_error)
        except ValueError:
            return cls.OTHER


class StarterRefused(Exception):  # noqa: N818 - it names the refusal, not an error class
    """The starter's tokens cannot be had; raised to the backend's caller so that no request is made."""

    def __init__(self, refusal: GameRefusal) -> None:
        super().__init__(refusal.value)
        self.refusal = refusal


class StarterTokens:
    """Fetches the two tokens a run is sent with, from the issuer, for the session the starter's socket opened with."""

    def __init__(
        self,
        relying_party: OidcRelyingParty,
        sessions: SessionRecordStore,
        web_auth: WebAuthConfig,
        workload_scope: str,
    ) -> None:
        self._relying_party = relying_party
        self._sessions = sessions
        self._web_auth = web_auth
        self._workload_scope = workload_scope

    async def asker_for(self, starter: HTTPConnection) -> Asker:
        """The starter as the backend's `Asker`; raises `StarterRefused` when the socket has no standing session."""
        session_id = session_id_from_request(starter, self._web_auth)
        if session_id is None:
            raise StarterRefused(GameRefusal.SIGN_IN_AGAIN)
        record = await asyncio.to_thread(self._sessions.load, session_id)
        if record is None:
            raise StarterRefused(_refusal_of_person_token(AccessTokenRefusal(AccessTokenRefusalReason.SESSION_ENDED)))
        # webauth binds the store to one issuer, and an account's id is that issuer's `sub`.
        principal = CapabilityPrincipal(iss=self._relying_party.issuer, sub=record.user.id)
        return Asker(principal, partial(self._tokens, session_id))

    async def _tokens(self, session_id: str) -> RunTokens:
        # Both grants block on the issuer, so each runs in a worker. A cancelled run cancels the await,
        # not the worker: its token is dropped unread and no later step runs.
        person = await asyncio.to_thread(self._person_token, session_id)
        if isinstance(person, AccessTokenRefusal):
            raise StarterRefused(_refusal_of_person_token(person))
        workload = await asyncio.to_thread(self._relying_party.workload_access_token, scopes=(self._workload_scope,))
        if isinstance(workload, OidcRefusal):
            _log_token_refusal(RunToken.WORKLOAD, workload)
            raise StarterRefused(GameRefusal.UNAVAILABLE)
        return RunTokens(person=person, workload=workload)

    def _person_token(self, session_id: str) -> SecretStr | AccessTokenRefusal:
        return fresh_access_token(
            self._relying_party,
            session_id,
            session_transaction=lambda: nullcontext(self._sessions),
            config=self._web_auth,
        )


class QuizGameMaster:
    """Asks the quiz agent for the game master's next line through the backend, on the starter's own access."""

    def __init__(self, backend: QuizBackend, tokens: StarterTokens) -> None:
        self._backend = backend
        self._tokens = tokens
        self._agent = load_agent(QUIZ_AGENT_FILE, list(TOOLS))

    async def ask(self, starter: HTTPConnection, transcript: tuple[Turn, ...]) -> GameOutcome:
        request = self._agent.request(_turn_messages(transcript))
        try:
            asker = await self._tokens.asker_for(starter)
            async with aclosing(self._backend.run(request, asker)) as events:
                async for event in events:
                    outcome = _outcome_of_event(event)
                    if outcome is not None:
                        return outcome
        except StarterRefused as refused:
            return Refused(refused.refusal)
        except TurnStopped as stopped:
            return _refusal_of_stop(stopped)
        _log_refusal(failure="no_final_event")
        return UNAVAILABLE


def _turn_messages(transcript: tuple[Turn, ...]) -> list[TurnMessage]:
    return [TurnMessage(role=ROLE_OF_SPEAKER[turn.speaker], text=turn.text) for turn in transcript]


def _outcome_of_event(event: object) -> GameOutcome | None:
    # Text before the end stays unread: only the final text becomes a line.
    match event:
        case FinalEvent(text=text) if text:
            return Line(_cut(text))
        case FinalEvent():
            _log_refusal(failure="no_words")
        case ToolCallEvent() | ToolResultEvent():
            _log_refusal(failure="tool_event")
        case AssistantTextEvent():
            return None
        case _:  # pragma: no cover - the backend yields no other event
            return None
    return UNAVAILABLE


def _refusal_of_stop(stopped: TurnStopped) -> Refused:
    code = stopped.code
    _log_refusal(kind=stopped.kind.value, code=None if code is None else code.value, detail=stopped.detail)
    if stopped.kind is not TurnStopKind.REFUSED:
        return UNAVAILABLE
    if code in NO_ACCESS_CODES:
        return Refused(GameRefusal.NO_ACCESS)
    if code is RejectionCode.BUSY:
        return Refused(GameRefusal.BUSY, retry_after_seconds=_busy_wait(stopped.retry_after_s))
    return UNAVAILABLE


def _busy_wait(retry_after_seconds: int | None) -> int:
    if retry_after_seconds is None:
        return GAME_RETRY_AFTER_FALLBACK_SECONDS
    return max(1, retry_after_seconds)


def _cut(text: str) -> str:
    if len(text) <= GAME_LINE_MAX_CHARACTERS:
        return text
    return text[: GAME_LINE_MAX_CHARACTERS - len(CUT_MARK)] + CUT_MARK


def _log_refusal(**codes: str | int | None) -> None:
    # Only closed values: a body, a message or an exception's text may carry the person's token or the game's text.
    logger.info("agent_service_refused", **{name: code for name, code in codes.items() if code is not None})


def _refusal_of_person_token(refusal: AccessTokenRefusal) -> GameRefusal:
    _log_token_refusal(RunToken.PERSON, refusal.issuer_refusal, reason=refusal.reason)
    match refusal.reason:
        case AccessTokenRefusalReason.SESSION_ENDED | AccessTokenRefusalReason.SIGN_IN_AGAIN:
            return GameRefusal.SIGN_IN_AGAIN
        case AccessTokenRefusalReason.ISSUER_UNAVAILABLE:
            return GameRefusal.UNAVAILABLE
        case _:  # pragma: no cover - the type checker proves the match exhaustive
            assert_never(refusal.reason)


def _log_token_refusal(
    token: RunToken, issuer_refusal: OidcRefusal | None, *, reason: AccessTokenRefusalReason | None = None
) -> None:
    codes = {
        "reason": None if reason is None else reason.value,
        "issuer_reason": None if issuer_refusal is None else issuer_refusal.reason.value,
        "idp_error": _issuer_error(issuer_refusal),
    }
    logger.info(
        "game_master_token_refused",
        token=token.value,
        **{name: code for name, code in codes.items() if code},
    )


def _issuer_error(issuer_refusal: OidcRefusal | None) -> str | None:
    idp_error = None if issuer_refusal is None else issuer_refusal.idp_error
    return None if idp_error is None else IssuerError.of(idp_error).value
