import asyncio
from contextlib import aclosing, nullcontext
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Final, assert_never

import structlog
from agent_runtime_client.agents import load_agent
from agent_runtime_client.backend import AgentBackend, TurnEvent, TurnPerson, TurnStopKind, TurnStopped
from agent_runtime_client.backend.gateway import GatewayBackend
from agent_runtime_client.turn import AgentRole, Principal, RejectionCode, TurnMessage, TurnRole
from agent_runtime_client.turn_events import AssistantTextEvent, FinalEvent, ToolCallEvent, ToolResultEvent
from webauth.dependencies import session_id_from_request
from webauth.oidc_login import AccessTokenRefusal, AccessTokenRefusalReason, current_groups

from hopin.agent.tools import TOOLS
from hopin.game.quiz import Speaker, Turn
from hopin.game.table import GameOutcome, Line, Refused
from hopin.game.vocabulary import (
    GAME_LINE_MAX_CHARACTERS,
    GAME_RETRY_AFTER_FALLBACK_SECONDS,
    GameRefusal,
)

if TYPE_CHECKING:
    from starlette.requests import HTTPConnection
    from webauth.config import WebAuthConfig
    from webauth.oidc import OidcRefusal, OidcRelyingParty
    from webauth.ports import SessionRecordStore

    from hopin.agent.settings import AgentSettings

type QuizBackend = AgentBackend

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

QUIZ_AGENT_FILE: Final = Path(__file__).parent.parent / "agents" / "quiz.md"
CUT_MARK: Final = "…"
UNAVAILABLE: Final = Refused(GameRefusal.UNAVAILABLE)
ROLE_OF_SPEAKER: Final = {Speaker.HOPIN: TurnRole.USER, Speaker.GAME_MASTER: TurnRole.ASSISTANT}
NO_ACCESS_CODES: Final = frozenset({RejectionCode.NOT_ENABLED, RejectionCode.FORBIDDEN})


def gateway_backend(settings: AgentSettings) -> QuizBackend:
    return GatewayBackend(settings.agent)


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


class StarterRefusedError(Exception):
    """The starter's standing cannot be read; raised to the backend's caller so that no request is made."""

    def __init__(self, refusal: GameRefusal) -> None:
        super().__init__(refusal.value)
        self.refusal = refusal


class StarterPerson:
    """Reads who a run is for, from the session the starter's socket opened with."""

    def __init__(self, relying_party: OidcRelyingParty, sessions: SessionRecordStore, web_auth: WebAuthConfig) -> None:
        self._relying_party = relying_party
        self._sessions = sessions
        self._web_auth = web_auth

    async def person_of(self, starter: HTTPConnection) -> TurnPerson:
        """The starter as the backend's `TurnPerson`; raises `StarterRefusedError` without a standing session."""
        session_id = session_id_from_request(starter, self._web_auth)
        if session_id is None:
            raise StarterRefusedError(GameRefusal.SIGN_IN_AGAIN)
        record = await asyncio.to_thread(self._sessions.load, session_id)
        if record is None:
            raise StarterRefusedError(_refusal_of_groups(AccessTokenRefusal(AccessTokenRefusalReason.SESSION_ENDED)))
        # webauth binds the store to one issuer, and an account's id is that issuer's `sub`. Two spellings
        # of the issuer would split one person's budget, so it goes out exactly as the relying party holds it.
        principal = Principal(iss=self._relying_party.issuer, sub=record.user.id)
        role: AgentRole = "admin" if record.user.role == self._web_auth.admin_role else "user"
        return TurnPerson(principal, role, partial(self._groups, session_id))

    async def _groups(self, session_id: str) -> frozenset[str]:
        # The grant blocks on the issuer, so it runs in a worker. A cancelled run cancels the await,
        # not the worker: it still stores the rotated refresh token, and no later step runs.
        groups = await asyncio.to_thread(self._current_groups, session_id)
        if isinstance(groups, AccessTokenRefusal):
            raise StarterRefusedError(_refusal_of_groups(groups))
        return frozenset(groups)

    def _current_groups(self, session_id: str) -> tuple[str, ...] | AccessTokenRefusal:
        return current_groups(
            self._relying_party,
            session_id,
            session_transaction=lambda: nullcontext(self._sessions),
            config=self._web_auth,
        )


class QuizGameMaster:
    """Asks the quiz agent for the game master's next line through the backend, for the starter."""

    def __init__(self, backend: QuizBackend, starter_person: StarterPerson) -> None:
        self._backend = backend
        self._starter_person = starter_person
        self._agent = load_agent(QUIZ_AGENT_FILE, list(TOOLS))

    async def ask(self, starter: HTTPConnection, transcript: tuple[Turn, ...]) -> GameOutcome:
        request = self._agent.request(_turn_messages(transcript))
        try:
            person = await self._starter_person.person_of(starter)
            async with aclosing(self._backend.run(request, person)) as events:
                async for event in events:
                    outcome = _outcome_of_event(event)
                    if outcome is not None:
                        return outcome
        except StarterRefusedError as refused:
            return Refused(refused.refusal)
        except TurnStopped as stopped:
            return _refusal_of_stop(stopped)
        _log_refusal(failure="no_final_event")
        return UNAVAILABLE


def _turn_messages(transcript: tuple[Turn, ...]) -> list[TurnMessage]:
    return [TurnMessage(role=ROLE_OF_SPEAKER[turn.speaker], text=turn.text) for turn in transcript]


def _outcome_of_event(event: TurnEvent) -> GameOutcome | None:
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
        case _:  # pragma: no cover - the type checker proves the match exhaustive
            assert_never(event)
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


def _log_refusal(**codes: str | None) -> None:
    # Only closed values: a body, a message or an exception's text may carry the game's text.
    logger.info("game_master_turn_refused", **{name: code for name, code in codes.items() if code is not None})


def _refusal_of_groups(refusal: AccessTokenRefusal) -> GameRefusal:
    _log_groups_refusal(refusal)
    match refusal.reason:
        case AccessTokenRefusalReason.SESSION_ENDED | AccessTokenRefusalReason.SIGN_IN_AGAIN:
            return GameRefusal.SIGN_IN_AGAIN
        case AccessTokenRefusalReason.ISSUER_UNAVAILABLE:
            return GameRefusal.UNAVAILABLE
        case _:  # pragma: no cover - the type checker proves the match exhaustive
            assert_never(refusal.reason)


def _log_groups_refusal(refusal: AccessTokenRefusal) -> None:
    issuer_refusal = refusal.issuer_refusal
    codes = {
        "issuer_reason": None if issuer_refusal is None else issuer_refusal.reason.value,
        "idp_error": _issuer_error(issuer_refusal),
    }
    logger.info(
        "game_master_groups_refused",
        reason=refusal.reason.value,
        **{name: code for name, code in codes.items() if code},
    )


def _issuer_error(issuer_refusal: OidcRefusal | None) -> str | None:
    idp_error = None if issuer_refusal is None else issuer_refusal.idp_error
    return None if idp_error is None else IssuerError.of(idp_error).value
