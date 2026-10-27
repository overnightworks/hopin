import logging
import re
from contextlib import aclosing
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Final

import httpx2
import structlog
from agent_runtime_client.agents import load_agent
from agent_runtime_client.run_stream import StreamViolation
from agent_runtime_client.sse import RunStreamDecoder
from agent_runtime_client.wire import (
    Cancelled,
    Failed,
    RejectionCode,
    RunRejection,
    Succeeded,
    ToolCall,
    ToolResult,
    TurnMessage,
    TurnRole,
)
from pydantic import ValidationError

from hopin.config.constants import (
    GAME_LINE_MAX_CHARACTERS,
    GAME_READ_TIMEOUT_SECONDS,
    GAME_REJECTION_MAX_BYTES,
    GAME_RETRY_AFTER_FALLBACK_SECONDS,
    GAME_RETRY_AFTER_MAX_SECONDS,
    GameRefusal,
)
from hopin.signaling.game import GameOutcome, Question, Refused

if TYPE_CHECKING:
    from collections.abc import Callable

    from agent_runtime_client.sse import Decoded
    from starlette.websockets import WebSocket

    from hopin.agents.tokens import RunTokens, StarterTokens

type AgentTransport = Callable[[], httpx2.AsyncBaseTransport]

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

QUIZ_AGENT_FILE: Final = Path(__file__).parent / "quiz.md"
NEXT_QUESTION_REQUEST: Final = "Stelle die nächste Quizfrage."
RUNS_PATH: Final = "/runs"
EVENT_STREAM: Final = "text/event-stream"
CUT_MARK: Final = "…"
TRANSPORT_TIMEOUT_SECONDS: Final = 5
TIMEOUT: Final = httpx2.Timeout(
    connect=TRANSPORT_TIMEOUT_SECONDS,
    write=TRANSPORT_TIMEOUT_SECONDS,
    pool=TRANSPORT_TIMEOUT_SECONDS,
    read=GAME_READ_TIMEOUT_SECONDS,
)
WHOLE_SECONDS: Final = re.compile(r"\d+", re.ASCII)
UNAVAILABLE: Final = Refused(GameRefusal.UNAVAILABLE)
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


for _logger_name in TRANSPORT_LOGGERS:
    logging.getLogger(_logger_name).addFilter(_unlogged)


class AgentServiceGameMaster:
    """Asks the agent service's quiz agent for the next question, on the starter's own access."""

    def __init__(self, *, agent_service_url: str, tokens: StarterTokens, transport: AgentTransport) -> None:
        self._runs_url = agent_service_url + RUNS_PATH
        self._tokens = tokens
        self._transport = transport
        self._agent = load_agent(QUIZ_AGENT_FILE, [])

    async def ask(self, starter: WebSocket, questions: tuple[str, ...]) -> GameOutcome:
        tokens = await self._tokens.of(starter)
        if isinstance(tokens, GameRefusal):
            return Refused(tokens)
        return await self._run(self._agent.request(_turns_after(questions)).model_dump_json(), tokens)

    async def _run(self, body: str, tokens: RunTokens) -> GameOutcome:
        # Each run owns its client and transport: closing a client closes its transport's pool,
        # which would cut every other room's stream on a shared one.
        async with httpx2.AsyncClient(
            transport=self._transport(), trust_env=False, follow_redirects=False, timeout=TIMEOUT
        ) as client:
            try:
                async with client.stream(
                    "POST", self._runs_url, content=body, headers=_run_headers(tokens)
                ) as response:
                    if response.status_code != HTTPStatus.OK or not _is_event_stream(response):
                        return await _refusal_of(response)
                    return await _outcome_of_stream(response)
            except httpx2.HTTPError as error:
                _log_refusal(error=type(error).__name__)
                return UNAVAILABLE


def _turns_after(questions: tuple[str, ...]) -> list[TurnMessage]:
    asked = [
        turn
        for question in questions
        for turn in (
            TurnMessage(role=TurnRole.USER, text=NEXT_QUESTION_REQUEST),
            TurnMessage(role=TurnRole.ASSISTANT, text=question),
        )
    ]
    return [*asked, TurnMessage(role=TurnRole.USER, text=NEXT_QUESTION_REQUEST)]


def _run_headers(tokens: RunTokens) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {tokens.person.get_secret_value()}",
        "Agent-Workload-Token": tokens.workload.get_secret_value(),
        "Accept": EVENT_STREAM,
        "Content-Type": "application/json",
    }


def _is_event_stream(response: httpx2.Response) -> bool:
    media_type = response.headers.get("content-type", "").partition(";")[0]
    return media_type.strip().lower() == EVENT_STREAM


async def _refusal_of(response: httpx2.Response) -> Refused:
    body = await _rejection_body(response) if response.is_error else b""
    if body is None:
        _log_refusal(status=response.status_code, body="oversized")
        return UNAVAILABLE
    code = _rejection_code(body)
    _log_refusal(status=response.status_code, code=None if code is None else code.value)
    if response.status_code == HTTPStatus.FORBIDDEN and code in NO_ACCESS_CODES:
        return Refused(GameRefusal.NO_ACCESS)
    if response.status_code == HTTPStatus.TOO_MANY_REQUESTS and code is RejectionCode.BUSY:
        return Refused(GameRefusal.BUSY, retry_after_seconds=_retry_after_seconds(response))
    return UNAVAILABLE


async def _rejection_body(response: httpx2.Response) -> bytes | None:
    """The body, or None once it outgrows a rejection; the rest stays unread and the connection closes with the run."""
    body = bytearray()
    async with aclosing(response.aiter_bytes()) as chunks:
        async for chunk in chunks:
            body += chunk
            if len(body) > GAME_REJECTION_MAX_BYTES:
                return None
    return bytes(body)


def _rejection_code(body: bytes) -> RejectionCode | None:
    try:
        return RunRejection.model_validate_json(body).code
    except ValidationError:
        return None


def _retry_after_seconds(response: httpx2.Response) -> int:
    shown = response.headers.get("retry-after", "")
    if WHOLE_SECONDS.fullmatch(shown) is None:
        return GAME_RETRY_AFTER_FALLBACK_SECONDS
    digits = shown.lstrip("0") or "0"
    if len(digits) > len(str(GAME_RETRY_AFTER_MAX_SECONDS)):
        return GAME_RETRY_AFTER_MAX_SECONDS
    return max(1, min(int(digits), GAME_RETRY_AFTER_MAX_SECONDS))


async def _outcome_of_stream(response: httpx2.Response) -> GameOutcome:
    decoder = RunStreamDecoder()
    async for chunk in response.aiter_bytes():
        for item in decoder.feed(chunk):
            outcome = _outcome_of_event(item)
            if outcome is not None:
                return outcome
    # The stream ended before its terminal event; the decoder reports that as a violation, logged here.
    for violation in decoder.close():
        _outcome_of_event(violation)
    return UNAVAILABLE


def _outcome_of_event(item: Decoded) -> GameOutcome | None:
    match item:
        case Succeeded(text=text):
            return Question(_cut(text))
        case Failed(code=code):
            _log_refusal(failure=code.value)
        case Cancelled():
            _log_refusal(failure="cancelled")
        case ToolCall() | ToolResult():
            _log_refusal(failure="tool_event")
        case StreamViolation(kind=kind):
            _log_refusal(violation=kind.value)
        case _:
            return None
    return UNAVAILABLE


def _cut(text: str) -> str:
    if len(text) <= GAME_LINE_MAX_CHARACTERS:
        return text
    return text[: GAME_LINE_MAX_CHARACTERS - len(CUT_MARK)] + CUT_MARK


def _log_refusal(**codes: str | int | None) -> None:
    # Only code-shaped values: a body, a message, a violation's detail or an exception's text may
    # carry the person's token or the game's text.
    logger.info("agent_service_refused", **{name: code for name, code in codes.items() if code is not None})
