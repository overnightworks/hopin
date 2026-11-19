"""A speech service's Realtime transcription endpoint on a loopback port: it records each session's headers and client
events, says what a test scripts, and refuses an upgrade when a test asks it to."""

import logging
import queue
import socket
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from speech_client.realtime_wire import (
    ErrorEvent,
    SessionCreated,
    SpeechStarted,
    SpeechStopped,
    TranscriptCompleted,
    TranscriptDelta,
    TranscriptionSession,
    error_event,
)
from speech_client.wire import REALTIME_PATH, RefusalCode, refusal
from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

if TYPE_CHECKING:
    from collections.abc import Iterator
    from http import HTTPStatus

    from speech_client.realtime_wire import ServerEvent
    from websockets.http11 import Request, Response
    from websockets.sync.server import Server, ServerConnection

LOCALHOST = "127.0.0.1"
WAIT_SECONDS = 10
PEEK_BYTES = 65536
UPGRADE_HEADER = b"upgrade: websocket"
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

    def app_has_spoken(self) -> bool:
        """Whether the app has sent its session update, the first thing it says once the connection is open."""
        with self._arrived:
            return bool(self.client_messages)

    def wait_until_closed(self) -> None:
        assert self.closed.wait(WAIT_SECONDS), "the connection to the service stayed open"

    def received(self, message: str) -> None:
        with self._arrived:
            self.client_messages.append(message)
            self._arrived.notify_all()


def a_sentence(
    text: str,
    *,
    started_ms: int | None = 0,
    stopped_ms: int | None = 900,
    item_id: str = ITEM,
    traceparent: str | None = None,
) -> Iterator[ServerEvent]:
    """The events of one finished item, the way the service sends them: its speech's bounds, a delta, the transcript,
    which carries the turn's `traceparent` when the service sends one."""
    if started_ms is not None:
        yield SpeechStarted(item_id=item_id, audio_start_ms=started_ms)
    if stopped_ms is not None:
        yield SpeechStopped(item_id=item_id, audio_end_ms=stopped_ms)
    yield TranscriptDelta(item_id=item_id, delta=text)
    yield completed(text, item_id=item_id, traceparent=traceparent)


def completed(text: str, *, item_id: str = ITEM, traceparent: str | None = None) -> TranscriptCompleted:
    return TranscriptCompleted.model_validate(
        {
            "item_id": item_id,
            "transcript": text,
            "usage": {"seconds": 1.0},
            "languages": [{"code": "de"}],
            "traceparent": traceparent,
        }
    )


def failing(code: RefusalCode) -> ErrorEvent:
    return error_event(code)


def _pipe(source: socket.socket, sink: socket.socket) -> None:
    """Copy what `source` sends to `sink` until `source` ends."""
    try:
        while chunk := source.recv(PEEK_BYTES):
            sink.sendall(chunk)
        sink.shutdown(socket.SHUT_WR)
    except OSError:
        pass


class _Front:
    """One port in front of the Realtime server and an HTTP origin, as the speech service has one origin for both: a
    connection that asks to upgrade to a WebSocket goes to the Realtime server, any other to the origin."""

    def __init__(self, realtime_port: int, http_port: int) -> None:
        self._ports = (realtime_port, http_port)
        self._listener = socket.create_server((LOCALHOST, 0))
        self.port = self._listener.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self) -> None:
        self._listener.close()

    def _accept(self) -> None:
        while True:
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._forward, args=(client,), daemon=True).start()

    def _forward(self, client: socket.socket) -> None:
        head = b""
        while b"\r\n\r\n" not in head:
            head = client.recv(PEEK_BYTES, socket.MSG_PEEK)
            if not head:
                client.close()
                return
        realtime, http = self._ports
        upstream = socket.create_connection((LOCALHOST, realtime if UPGRADE_HEADER in head.lower() else http))
        threading.Thread(target=_pipe, args=(upstream, client), daemon=True).start()
        _pipe(client, upstream)


class FakeSpeech:
    """The service: `sessions` fills as the app connects; `refusal` answers the next upgrades over HTTP, and while
    `opening` is clear an upgrade waits, so the app holds the sound it is sent. With the loopback port of an HTTP
    origin that speaks (its health and its sounds), the service's one origin serves both."""

    def __init__(self, speaking_port: int | None = None) -> None:
        self.sessions: list[Session] = []
        self._speaking_port = speaking_port
        self._front: _Front | None = None
        self.refusal: Refusal | None = None
        self.opening = threading.Event()
        self.opening.set()
        self._connected = threading.Condition()
        self._server: Server | None = None

    @property
    def origin(self) -> str:
        assert self._server is not None
        port = self._server.socket.getsockname()[1] if self._front is None else self._front.port
        return f"http://{LOCALHOST}:{port}"

    def start(self) -> None:
        self._server = serve(self._converse, LOCALHOST, 0, process_request=self._maybe_refuse, logger=_QUIET)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        if self._speaking_port is not None:
            self._front = _Front(self._server.socket.getsockname()[1], self._speaking_port)

    def stop(self) -> None:
        assert self._server is not None
        if self._front is not None:
            self._front.close()
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
            # The service answers only an app that has sent its session update; an event or a close sent before it
            # makes the app's update fail on a connection that is already shut.
            while session.app_has_spoken():
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
