"""The game master's backend: the starter's verified identity and groups, and one quiz turn on the gateway.

The turn logs only codes. `game_master_turn.ended` carries a refused turn's closed detail label, else its rejection or
failure code, or a closed reason of HopIn's own such as `no_words`; `group_read.ended` and `token_refresh.ended` carry
webauth's and the issuer's refusal reason, the issuer's error code when it is one of OAuth's token endpoint codes, else
`other`. No field carries a prompt, a question, a group, a key or what the gateway answered.
"""

import asyncio
import json
import logging
from contextlib import aclosing
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, assert_never

from agents.agent_file import load_agent
from agents.backend import AgentBackend, TurnEvent, TurnPerson, TurnStopped
from agents.backend.gateway import GatewayBackend
from agents.tools import HandTool, ToolDeclaration, ToolEffect, ToolOutcome, tool_parameters
from agents.turn import AgentRole, Principal, StopOutcome, TurnMessage, TurnRole
from agents.turn_events import AssistantTextEvent, FinalEvent, ToolCallEvent, ToolResultEvent
from logline import StepTimer
from pydantic import BaseModel, ConfigDict, ValidationError
from webauth.admission import AppRole
from webauth.dependencies import session_id_from_request
from webauth.oidc_login import AccessTokenRefusal, AccessTokenRefusalReason, fresh_access_token
from webauth.session_groups import SessionGroups

from hopin.agent.issuer_error import grant_refusal_reason
from hopin.config.constants import LINE_LIMITS
from hopin.game.quiz import Speaker, Turn, Verdict
from hopin.game.table import GameOutcome, Line, Refused
from hopin.game.vocabulary import (
    GAME_RETRY_AFTER_FALLBACK_SECONDS,
    GameRefusal,
)
from hopin.stores.session_transaction import session_transaction_of

if TYPE_CHECKING:
    from agents.tools import JsonObject
    from pydantic import SecretStr
    from starlette.requests import HTTPConnection
    from webauth.config import WebAuthConfig
    from webauth.oidc import OidcRelyingParty
    from webauth.ports import SessionRecordStore

    from hopin.agent.settings import AgentSettings
    from hopin.web.request_language import RequestLanguage


class QuizBackend(AgentBackend, Protocol):
    """The backend the game master asks; its host closes it once with `aclose`."""

    async def aclose(self) -> None:
        """Release the backend's connections."""


_STEPS: Final = logging.getLogger(__name__)

QUIZ_AGENT_FILE: Final = Path(__file__).parent.parent / "agents" / "quiz.md"
CUT_MARK: Final = "…"
UNAVAILABLE: Final = Refused(GameRefusal.UNAVAILABLE)
ROLE_OF_SPEAKER: Final = {Speaker.HOPIN: TurnRole.USER, Speaker.GAME_MASTER: TurnRole.ASSISTANT}
# The run's language follows the starter's interface, since a click and no message of theirs caused it; like HopIn's
# requests it is agent input and stays German.
STARTER_LANGUAGE_SYSTEM: Final = "Schreibe jede Zeile in der Sprache mit dem Sprach-Tag {tag}."


VERDICT_TOOL_NAME: Final = "record_verdicts"


class _Verdicts(BaseModel):
    """The input of the verdict tool, which is how the game master hands in its judgement of each answer."""

    model_config = ConfigDict(extra="forbid", strict=True)

    verdicts: list[Verdict]


class MalformedVerdictsError(Exception):
    """The game master's verdicts are not the tool's input; the message holds no provider output."""


async def _acknowledge_verdicts(_principal: Principal, _arguments: JsonObject) -> ToolOutcome:
    return ToolOutcome(content="Urteile erhalten.", is_error=False)


VERDICT_TOOL: Final = HandTool(
    ToolDeclaration(
        name=VERDICT_TOOL_NAME,
        description=(
            "Gib dein Urteil über die Antworten der Mitspielenden ab, genau einmal je Auflösung und vor der "
            "Auflösung selbst: je Antwort ihre nr aus der Bitte und ob sie richtig (correct) oder falsch (wrong) ist."
        ),
        parameters=tool_parameters(_Verdicts),
        effect=ToolEffect.READ_ONLY,
    ),
    _acknowledge_verdicts,
)


def verdicts_of(arguments: JsonObject) -> tuple[Verdict, ...]:
    """The verdicts a call of the verdict tool holds; raises `MalformedVerdictsError` for any other input."""
    try:
        return tuple(_Verdicts.model_validate_json(json.dumps(arguments), strict=True).verdicts)
    except ValidationError as error:
        raise MalformedVerdictsError from error


def gateway_backend(settings: AgentSettings) -> QuizBackend:
    return GatewayBackend(settings.agent, tools=[VERDICT_TOOL])


class StarterRefusedError(Exception):
    """The starter's standing cannot be read; raised to the backend's caller so that no request is made."""

    def __init__(self, refusal: GameRefusal) -> None:
        super().__init__(refusal.value)
        self.refusal = refusal


class StarterPerson:
    """Reads who a run is for and in which language, from the session the starter's socket opened with."""

    def __init__(
        self,
        relying_party: OidcRelyingParty,
        sessions: SessionRecordStore,
        web_auth: WebAuthConfig,
        request_language: RequestLanguage,
    ) -> None:
        self._relying_party = relying_party
        self._sessions = sessions
        self._web_auth = web_auth
        self._session_transaction = session_transaction_of(sessions)
        self._groups = SessionGroups(relying_party, self._session_transaction, web_auth)
        self._request_language = request_language

    async def language_of(self, starter: HTTPConnection) -> str:
        """The starter's language tag, as every page of theirs reads it."""
        return await asyncio.to_thread(self._request_language, starter)

    async def person_of(self, starter: HTTPConnection) -> TurnPerson:
        """The starter as the backend's `TurnPerson`; raises `StarterRefusedError` without a standing session."""
        session_id = self._session_id_of(starter)
        record = await asyncio.to_thread(self._sessions.load, session_id)
        if record is None:
            raise StarterRefusedError(_refusal_of_grant(AccessTokenRefusal(AccessTokenRefusalReason.SESSION_ENDED)))
        # webauth binds the store to one issuer, and an account's id is that issuer's `sub`. Two spellings
        # of the issuer would split one person's budget, so it goes out exactly as the relying party holds it.
        principal = Principal(iss=self._relying_party.issuer, sub=record.user.id)
        role: AgentRole = "admin" if record.user.role == AppRole.ADMIN else "user"
        return TurnPerson(principal, role, partial(self._groups_of, session_id))

    async def access_token_of(self, starter: HTTPConnection) -> str:
        """A new access token of the starter, from one refresh grant; raises `StarterRefusedError` without one."""
        session_id = self._session_id_of(starter)
        # Like the groups grant, it runs in a worker that still stores a rotated refresh token when cancelled.
        async with StepTimer("token_refresh.ended", _STEPS) as step:
            token = await asyncio.to_thread(self._fresh_access_token, session_id)
            if isinstance(token, AccessTokenRefusal):
                step.refuse(grant_refusal_reason(token))
        if isinstance(token, AccessTokenRefusal):
            raise StarterRefusedError(_refusal_of_grant(token))
        return token.get_secret_value()

    def _session_id_of(self, starter: HTTPConnection) -> str:
        session_id = session_id_from_request(starter, self._web_auth)
        if session_id is None:
            raise StarterRefusedError(GameRefusal.SIGN_IN_AGAIN)
        return session_id

    async def _groups_of(self, session_id: str) -> frozenset[str]:
        async with StepTimer("group_read.ended", _STEPS) as step:
            groups = await self._groups.of_session(session_id)
            if isinstance(groups, AccessTokenRefusal):
                step.refuse(grant_refusal_reason(groups))
        if isinstance(groups, AccessTokenRefusal):
            raise StarterRefusedError(_refusal_of_grant(groups))
        return groups

    def _fresh_access_token(self, session_id: str) -> SecretStr | AccessTokenRefusal:
        return fresh_access_token(
            self._relying_party,
            session_id,
            session_transaction=self._session_transaction,
            config=self._web_auth,
        )


class QuizGameMaster:
    """Asks the quiz agent for the game master's next line through the backend, for the starter, in the starter's
    language."""

    def __init__(self, backend: QuizBackend, starter_person: StarterPerson) -> None:
        self._backend = backend
        self._starter_person = starter_person
        self._agent = load_agent(QUIZ_AGENT_FILE, [VERDICT_TOOL.declaration])

    async def ask(self, starter: HTTPConnection, transcript: tuple[Turn, ...]) -> GameOutcome:
        async with StepTimer("game_master_turn.ended", _STEPS) as step:
            try:
                tag = await self._starter_person.language_of(starter)
                request = self._agent.request(
                    _turn_messages(transcript), appended_system=STARTER_LANGUAGE_SYSTEM.format(tag=tag)
                )
                person = await self._starter_person.person_of(starter)
                verdicts: tuple[Verdict, ...] | None = None
                async with aclosing(self._backend.run(request, person)) as events:
                    async for event in events:
                        if isinstance(event, ToolCallEvent) and event.name == VERDICT_TOOL_NAME:
                            verdicts = _verdicts_of_call(event, verdicts)
                            continue
                        outcome = _outcome_of_event(event, step, verdicts or ())
                        if outcome is not None:
                            return outcome
            except MalformedVerdictsError:
                step.refuse("malformed_verdicts")
                return UNAVAILABLE
            except StarterRefusedError as refused:
                step.refuse(refused.refusal.value)
                return Refused(refused.refusal)
            except TurnStopped as stopped:
                return _refusal_of_stop(stopped, step)
            step.refuse("no_final_event")
            return UNAVAILABLE

    async def aclose(self) -> None:
        await self._backend.aclose()


def _turn_messages(transcript: tuple[Turn, ...]) -> list[TurnMessage]:
    return [TurnMessage(role=ROLE_OF_SPEAKER[turn.speaker], text=turn.text) for turn in transcript]


def _verdicts_of_call(call: ToolCallEvent, earlier: tuple[Verdict, ...] | None) -> tuple[Verdict, ...]:
    """The verdicts of the turn's one call of the verdict tool; a second call is as malformed as a bad one."""
    if earlier is not None:
        raise MalformedVerdictsError
    return verdicts_of(call.input)


def _outcome_of_event(event: TurnEvent, step: StepTimer, verdicts: tuple[Verdict, ...]) -> GameOutcome | None:
    # Text before the end stays unread: only the final text becomes a line.
    match event:
        case FinalEvent(text=text) if text:
            return Line(_cut(text), verdicts)
        case FinalEvent():
            step.refuse("no_words")
        case ToolCallEvent():
            step.refuse("tool_event")
        case ToolResultEvent() | AssistantTextEvent():
            return None
        case _:  # pragma: no cover - the type checker proves the match exhaustive
            assert_never(event)
    return UNAVAILABLE


def _refusal_of_stop(stopped: TurnStopped, step: StepTimer) -> Refused:
    step.refuse(stopped.detail or stopped.code.value)
    outcome = stopped.outcome
    match outcome:
        case StopOutcome.NOT_ENABLED:
            return Refused(GameRefusal.NO_ACCESS)
        case StopOutcome.BUSY:
            return Refused(GameRefusal.BUSY, retry_after_seconds=_busy_wait(stopped.retry_after_s))
        case StopOutcome.BUDGET_SPENT:
            return Refused(GameRefusal.BUDGET_SPENT)
        case StopOutcome.UNAVAILABLE:
            return UNAVAILABLE
        case _:  # pragma: no cover - the type checker proves the match exhaustive
            assert_never(outcome)


def _busy_wait(retry_after_seconds: int | None) -> int:
    if retry_after_seconds is None:
        return GAME_RETRY_AFTER_FALLBACK_SECONDS
    return max(1, retry_after_seconds)


def _cut(text: str) -> str:
    if len(text) <= LINE_LIMITS.max_characters:
        return text
    return text[: LINE_LIMITS.max_characters - len(CUT_MARK)] + CUT_MARK


def _refusal_of_grant(refusal: AccessTokenRefusal) -> GameRefusal:
    match refusal.reason:
        case AccessTokenRefusalReason.SESSION_ENDED | AccessTokenRefusalReason.SIGN_IN_AGAIN:
            return GameRefusal.SIGN_IN_AGAIN
        case AccessTokenRefusalReason.ISSUER_UNAVAILABLE:
            return GameRefusal.UNAVAILABLE
        case _:  # pragma: no cover - the type checker proves the match exhaustive
            assert_never(refusal.reason)
