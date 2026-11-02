"""An agent service that exists only in a test: it checks both tokens with the fake issuer and answers from a script.

Each run gets a fresh `httpx2.MockTransport` from `transport`, the factory HopIn's adapter takes, so every run
owns its transport as it does in production. The script and the record are shared by all runs and all threads.
"""

import asyncio
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import TYPE_CHECKING

import httpx2
from agent_runtime_client.sse import encode_event
from agent_runtime_client.wire import (
    PROTOCOL_VERSION,
    AssistantText,
    Cancelled,
    Failed,
    FailureCode,
    Heartbeat,
    RejectionCode,
    RouteKind,
    RunFile,
    RunRejection,
    RunRequest,
    RunStarted,
    Succeeded,
    ToolCall,
)
from pydantic import ValidationError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from tests.support.fake_issuer import FakeIssuer

DEFAULT_QUESTION = "Welcher Fluss fließt durch Lissabon?"
PERSONS_ACCESS_ECHO = "<<person-access>>"
HEARTBEAT_INTERVAL_SECONDS = 15
SERVED_MODEL = "anthropic/test-model"
SIGNAL_CAP_SECONDS = 10
EVENT_STREAM = "text/event-stream"


class Signal:
    """Set once from any thread; waited for from another thread or from a coroutine on any loop."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._set = threading.Event()
        self._waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = []

    def set(self) -> None:
        with self._guard:
            self._set.set()
            waiters, self._waiters = self._waiters, []
        for loop, event in waiters:
            loop.call_soon_threadsafe(event.set)

    def is_set(self) -> bool:
        return self._set.is_set()

    def wait(self) -> bool:
        return self._set.wait(SIGNAL_CAP_SECONDS)

    async def wait_async(self) -> None:
        event = asyncio.Event()
        with self._guard:
            if self._set.is_set():
                return
            self._waiters.append((asyncio.get_running_loop(), event))
        await event.wait()


@dataclass(frozen=True)
class SeenRun:
    """One request that reached the service: whose tokens it carried and what it asked."""

    subject: str | None
    person_token: str = field(repr=False)
    workload_admitted: bool
    header_names: frozenset[str]
    request: RunRequest | None
    closed: Signal = field(default_factory=Signal, compare=False)


@dataclass(frozen=True)
class Succeeds:
    text: str = DEFAULT_QUESTION
    files: tuple[RunFile, ...] = ()


@dataclass(frozen=True)
class Rejects:
    status: HTTPStatus
    code: RejectionCode
    message: str = "refused"
    retry_after: str | None = None


@dataclass(frozen=True)
class Fails:
    code: FailureCode = FailureCode.PROVIDER_ERROR
    message: str = "the provider failed"


@dataclass(frozen=True)
class CancelsTheRun:
    pass


@dataclass(frozen=True)
class CallsATool:
    pass


@dataclass(frozen=True)
class EndsEarly:
    """The stream ends after `run_started`, without a terminal event."""


@dataclass(frozen=True)
class SendsBytes:
    body: bytes
    status: HTTPStatus = HTTPStatus.OK
    content_type: str = EVENT_STREAM


@dataclass(frozen=True)
class Redirects:
    location: str
    status: HTTPStatus = HTTPStatus.TEMPORARY_REDIRECT


@dataclass(frozen=True, eq=False)
class Holds:
    """Sends `run_started`, then waits until released and answers `then`."""

    then: Succeeds | Fails = Succeeds()
    started: Signal = field(default_factory=Signal)
    release: Signal = field(default_factory=Signal)


type Answer = Succeeds | Rejects | Fails | CancelsTheRun | CallsATool | EndsEarly | SendsBytes | Redirects | Holds


class _EventStream(httpx2.AsyncByteStream):
    def __init__(self, chunks: list[bytes], seen: SeenRun, held: Holds | None = None) -> None:
        self._chunks = chunks
        self._seen = seen
        self._held = held

    async def __aiter__(self) -> AsyncIterator[bytes]:
        first, *rest = self._chunks
        yield first
        if self._held is not None:
            self._held.started.set()
            await self._held.release.wait_async()
        for chunk in rest:
            yield chunk

    async def aclose(self) -> None:
        self._seen.closed.set()


class FakeAgentService:
    def __init__(self, issuer: FakeIssuer) -> None:
        self._issuer = issuer
        self._guard = threading.Lock()
        self._script: deque[Answer] = deque()
        self._runs: list[SeenRun] = []
        self.transports_made = 0

    def answer_next(self, *answers: Answer) -> None:
        with self._guard:
            self._script.extend(answers)

    @property
    def queued_answers(self) -> tuple[Answer, ...]:
        with self._guard:
            return tuple(self._script)

    def reset(self) -> None:
        """Drops the queued answers and the recorded runs; the transport the server holds stays."""
        with self._guard:
            self._script.clear()
            self._runs.clear()

    @property
    def runs(self) -> list[SeenRun]:
        with self._guard:
            return list(self._runs)

    def transport(self) -> httpx2.AsyncBaseTransport:
        with self._guard:
            self.transports_made += 1
        return httpx2.MockTransport(self._handle)

    async def _handle(self, request: httpx2.Request) -> httpx2.Response:
        scheme, _, person_token = request.headers.get("authorization", "").partition(" ")
        subject = self._issuer.subject_of_access_token(person_token) if scheme == "Bearer" else None
        workload_admitted = self._issuer.is_workload_token(request.headers.get("agent-workload-token", ""))
        seen = SeenRun(
            subject=subject,
            person_token=person_token,
            workload_admitted=workload_admitted,
            header_names=frozenset(name.lower() for name in request.headers),
            request=_parsed(request.content),
        )
        with self._guard:
            self._runs.append(seen)
            answer = self._script.popleft() if self._script else Succeeds()
        if subject is None or not workload_admitted:
            return _rejection(Rejects(HTTPStatus.UNAUTHORIZED, RejectionCode.UNAUTHORIZED), person_token)
        return _response(answer, seen)


def _parsed(content: bytes) -> RunRequest | None:
    try:
        return RunRequest.model_validate_json(content)
    except ValidationError:
        return None


def _response(answer: Answer, seen: SeenRun) -> httpx2.Response:
    match answer:
        case Rejects():
            return _rejection(answer, seen.person_token)
        case SendsBytes(body=body, status=status, content_type=content_type):
            stream = _EventStream([body.replace(PERSONS_ACCESS_ECHO.encode(), seen.person_token.encode())], seen)
            return httpx2.Response(status, headers={"content-type": content_type}, stream=stream)
        case Redirects(location=location, status=status):
            return httpx2.Response(status, headers={"location": location})
        case Holds(then=then):
            return _event_stream(_events_of(then, seen.person_token), seen, held=answer)
        case _:
            return _event_stream(_events_of(answer, seen.person_token), seen)


def _rejection(rejection: Rejects, person_token: str) -> httpx2.Response:
    body = RunRejection(
        v=PROTOCOL_VERSION,
        code=rejection.code,
        message=rejection.message.replace(PERSONS_ACCESS_ECHO, person_token),
    )
    headers = {"content-type": "application/json"}
    if rejection.retry_after is not None:
        headers["retry-after"] = rejection.retry_after
    return httpx2.Response(rejection.status, headers=headers, content=body.model_dump_json().encode())


def _event_stream(events: list[object], seen: SeenRun, *, held: Holds | None = None) -> httpx2.Response:
    chunks = [encode_event(event) for event in events]
    return httpx2.Response(
        HTTPStatus.OK, headers={"content-type": EVENT_STREAM}, stream=_EventStream(chunks, seen, held)
    )


def _events_of(answer: Answer, person_token: str) -> list[object]:
    run_id = uuid.uuid4()
    started = RunStarted(
        v=PROTOCOL_VERSION,
        run_id=run_id,
        seq=0,
        model=SERVED_MODEL,
        route=RouteKind.CLI,
        heartbeat_interval_s=HEARTBEAT_INTERVAL_SECONDS,
    )
    match answer:
        case Succeeds(text=text, files=files):
            spoken = [AssistantText(v=PROTOCOL_VERSION, run_id=run_id, seq=2, text=text)] if text else []
            return [
                started,
                Heartbeat(v=PROTOCOL_VERSION, run_id=run_id, seq=1),
                *spoken,
                Succeeded(v=PROTOCOL_VERSION, run_id=run_id, seq=2 + len(spoken), text=text, files=files),
            ]
        case Fails(code=code, message=message):
            shown = message.replace(PERSONS_ACCESS_ECHO, person_token)
            return [started, Failed(v=PROTOCOL_VERSION, run_id=run_id, seq=1, code=code, message=shown)]
        case CancelsTheRun():
            return [started, Cancelled(v=PROTOCOL_VERSION, run_id=run_id, seq=1)]
        case CallsATool():
            call = ToolCall(
                v=PROTOCOL_VERSION,
                run_id=run_id,
                seq=1,
                tool_use_id="call-1",
                name="lookup",
                input={},
                effect="read_only",
            )
            return [started, call]
        case _:
            return [started]
