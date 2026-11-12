"""A speech service's Realtime transcription endpoint on a loopback port: it records each session's headers and client
events, says what a test scripts, and refuses an upgrade when a test asks it to."""

import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from speech_client.wire import (
    REALTIME_PATH,
    ErrorEvent,
    RefusalCode,
    SessionCreated,
    SpeechStarted,
    SpeechStopped,
    TranscriptCompleted,
    TranscriptDelta,
    TranscriptionSession,
    error_event,
    refusal,
)
from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

if TYPE_CHECKING:
    from collections.abc import Iterator
    from http import HTTPStatus

    from speech_client.wire import ServerEvent
    from websockets.http11 import Request, Response
    from websockets.sync.server import Server, ServerConnection

LOCALHOST = "127.0.0.1"
WAIT_SECONDS = 10
POLL_SECONDS = 0.02
ITEM = "item_one"
_QUIET = logging.getLogger("fake_speech")
_QUIET.setLevel(logging.CRITICAL)
AUDIO_APPEND_TYPE = "input_audio_buffer.append"
DROPPED = -1


@dataclass(frozen=True)
class Refusal:
    """An upgrade the service answers over HTTP instead of opening a session."""

    status: HTTPStatus
    body: str = ""
    headers: tuple[tuple[str, str], ...] = ()


@dataclass
class Session:
    """One Realtime connection as the service saw it: what the app sent and when it ended."""

    headers: dict[str, str]
    client_messages: list[str] = field(default_factory=list)
    closed: threading.Event = field(default_factory=threading.Event)
    _outgoing: queue.Queue[str | int | None] = field(default_factory=queue.Queue)
    _arrived: threading.Condition = field(default_factory=threading.Condition)

    def say(self, event: ServerEvent) -> None:
        """Send the app a server event."""
        self._outgoing.put(event.model_dump_json())

    def hang_up(self) -> None:
        """End the connection from the service's side, in order."""
        self._outgoing.put(None)

    def drop(self) -> None:
        """End the connection from the service's side with no close handshake, as a reset does."""
        self._outgoing.put(DROPPED)

    def audio_appends(self) -> int:
        with self._arrived:
            return sum(AUDIO_APPEND_TYPE in message for message in self.client_messages)

    def wait_for_audio(self, appends: int) -> None:
        """Block until the service has received at least `appends` audio frames."""
        with self._arrived:
            assert self._arrived.wait_for(
                lambda: sum(AUDIO_APPEND_TYPE in message for message in self.client_messages) >= appends,
                WAIT_SECONDS,
            ), f"the service received fewer than {appends} audio frames"

    def wait_until_closed(self) -> None:
        assert self.closed.wait(WAIT_SECONDS), "the connection to the service stayed open"

    def received(self, message: str) -> None:
        with self._arrived:
            self.client_messages.append(message)
            self._arrived.notify_all()


def a_sentence(text: str, *, started_ms: int | None = 0, stopped_ms: int | None = 900) -> Iterator[ServerEvent]:
    """The events of one finished item, the way the service sends them: its speech's bounds, a delta, the transcript."""
    if started_ms is not None:
        yield SpeechStarted(item_id=ITEM, audio_start_ms=started_ms)
    if stopped_ms is not None:
        yield SpeechStopped(item_id=ITEM, audio_end_ms=stopped_ms)
    yield TranscriptDelta(item_id=ITEM, delta=text)
    yield completed(text)


def completed(text: str, *, item_id: str = ITEM) -> TranscriptCompleted:
    return TranscriptCompleted.model_validate(
        {
            "item_id": item_id,
            "transcript": text,
            "usage": {"seconds": 1.0},
            "languages": [{"code": "de"}],
        }
    )


def failing(code: RefusalCode) -> ErrorEvent:
    return error_event(code)


class FakeSpeech:
    """The service: `sessions` fills as the app connects; `refusal` answers the next upgrades over HTTP, and while
    `opening` is clear an upgrade waits, so the app holds the sound it is sent."""

    def __init__(self) -> None:
        self.sessions: list[Session] = []
        self.refusal: Refusal | None = None
        self.opening = threading.Event()
        self.opening.set()
        self._connected = threading.Condition()
        self._server: Server | None = None

    @property
    def origin(self) -> str:
        assert self._server is not None
        return f"http://{LOCALHOST}:{self._server.socket.getsockname()[1]}"

    def start(self) -> None:
        self._server = serve(self._converse, LOCALHOST, 0, process_request=self._maybe_refuse, logger=_QUIET)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        assert self._server is not None
        self._server.shutdown()

    def next_session(self, number: int = 1) -> Session:
        """Block until the service holds `number` sessions, and answer the newest."""
        with self._connected:
            assert self._connected.wait_for(lambda: len(self.sessions) >= number, WAIT_SECONDS), "no session came"
            return self.sessions[number - 1]

    def _maybe_refuse(self, connection: ServerConnection, request: Request) -> Response | None:
        assert request.path.startswith(REALTIME_PATH)
        assert self.opening.wait(WAIT_SECONDS), "an upgrade was held for good"
        if self.refusal is None:
            return None
        response = connection.respond(self.refusal.status, self.refusal.body)
        for name, value in self.refusal.headers:
            response.headers[name] = value
        return response

    def _converse(self, connection: ServerConnection) -> None:
        session = Session(dict(connection.request.headers.items()))
        with self._connected:
            self.sessions.append(session)
            self._connected.notify_all()
        try:
            connection.send(SessionCreated(session=TranscriptionSession(type="transcription")).model_dump_json())
            self._serve(connection, session)
        except ConnectionClosed:
            pass
        finally:
            session.closed.set()

    @staticmethod
    def _serve(connection: ServerConnection, session: Session) -> None:
        while True:
            while True:
                try:
                    outgoing = session._outgoing.get_nowait()  # noqa: SLF001 - the fake's own session
                except queue.Empty:
                    break
                if outgoing is None:
                    connection.close()
                    return
                if outgoing == DROPPED:
                    connection.socket.close()
                    return
                connection.send(outgoing)
            try:
                message = connection.recv(timeout=POLL_SECONDS)
            except TimeoutError:
                continue
            session.received(message if isinstance(message, str) else message.decode())


def refused_with(code: RefusalCode, *, headers: tuple[tuple[str, str], ...] = ()) -> Refusal:
    """The service's refusal of `code`, with the status and body the wire gives it."""
    wired = refusal(code)
    return Refusal(wired.status, wired.model_dump_json(), headers)
