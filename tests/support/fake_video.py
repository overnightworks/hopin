"""The video service on a loopback port, and the TURN provider its sessions are handed a credential from, on
`video_client`'s types: `GET /health` and the session upgrade of WIRE-2 to WIRE-4 and WIRE-7. It records each session's
headers and client events, says what a test scripts, and refuses an upgrade or holds one back when a test asks it to.
"""

import json
import logging
import queue
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from video_client.session_wire import SessionCreated, SessionInfo, SessionUpdated, error_event
from video_client.wire import (
    HEALTH_PATH,
    LOOK_PARAM,
    REFUSAL_STATUS,
    SESSIONS_PATH,
    Health,
    LookHealth,
    ProviderHealth,
    RefusalCode,
    SessionsHealth,
    Verb,
    VideoFormat,
    refusal,
)
from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

from tests.support.arrivals import Arrivals

if TYPE_CHECKING:
    from collections.abc import Mapping

    from video_client.session_wire import ServerEvent
    from websockets.http11 import Request, Response
    from websockets.sync.server import Server, ServerConnection

LOCALHOST = "127.0.0.1"
WAIT_SECONDS = 10
POLL_SECONDS = 0.02
PICTURE = VideoFormat(width=256, height=256, frame_rate=25)
_QUIET = logging.getLogger("fake_video")
_QUIET.setLevel(logging.CRITICAL)


@dataclass(frozen=True)
class Refusal:
    """An upgrade the service answers over HTTP instead of opening a session."""

    status: HTTPStatus
    body: str


def refused_with(code: RefusalCode) -> Refusal:
    """The service's refusal of `code`, with the status and body the wire gives it."""
    return Refusal(HTTPStatus(REFUSAL_STATUS[code]), refusal(code).model_dump_json())


@dataclass
class Session:
    """One session as the service saw it: what the app sent and when it ended."""

    headers: dict[str, str]
    look: str
    client_messages: list[str] = field(default_factory=list)
    closed: threading.Event = field(default_factory=threading.Event)
    close_code: int | None = None
    _outgoing: queue.Queue[ServerEvent | str | bytes | None] = field(default_factory=queue.Queue)
    _arrived: threading.Condition = field(default_factory=threading.Condition)

    def say(self, message: ServerEvent | str | bytes) -> None:
        """Send the app a server event, or a message as it is, which is no event when it is not one."""
        self._outgoing.put(message)

    def fail(self, code: RefusalCode) -> None:
        """Send the app an `error` event, as the service does before it closes a failed session."""
        self.say(error_event(code))

    def hang_up(self) -> None:
        """End the connection from the service's side, in order."""
        self._outgoing.put(None)

    def wait_until_closed(self) -> None:
        assert self.closed.wait(WAIT_SECONDS), "the connection to the service stayed open"

    def is_closed(self) -> bool:
        return self.closed.is_set()

    def received(self, message: str) -> None:
        with self._arrived:
            self.client_messages.append(message)
            self._arrived.notify_all()

    def header(self, name: str) -> str:
        """The header the app upgraded with, however the app cased its name."""
        return next(value for header, value in self.headers.items() if header.lower() == name.lower())

    def wait_for_update(self) -> dict:
        """The `session.update` the app sent, once it has."""
        with self._arrived:
            assert self._arrived.wait_for(lambda: bool(self.client_messages), WAIT_SECONDS), "no update came"
            return json.loads(self.client_messages[0])


class FakeVideo:
    """The service: `sessions` fills as the app upgrades; `health_status` answers `GET /health`; `refusal` answers the
    next upgrades over HTTP; a clear `health_open` or `opening` holds that answer back, and with `updates` off the
    service never answers `session.update`, and otherwise it answers with `update_reply`."""

    def __init__(self, looks: Mapping[str, bool]) -> None:
        self.looks = dict(looks)
        self.sessions = Arrivals[Session](WAIT_SECONDS)
        self.health_requests = 0
        self.upgrades = 0
        self.health_asked = threading.Event()
        self.upgrade_asked = threading.Event()
        self.health_status = HTTPStatus.OK
        self.refusal: Refusal | None = None
        self.updates = True
        self.update_reply: ServerEvent = SessionUpdated()
        self.health_open = threading.Event()
        self.health_open.set()
        self.opening = threading.Event()
        self.opening.set()
        self._server: Server | None = None

    @property
    def origin(self) -> str:
        assert self._server is not None
        return f"http://{LOCALHOST}:{self._server.socket.getsockname()[1]}"

    def start(self) -> None:
        self._server = serve(self._converse, LOCALHOST, 0, process_request=self._answer, logger=_QUIET)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        assert self._server is not None
        self._server.shutdown()

    def next_session(self, number: int = 1) -> Session:
        """Block until the service holds `number` sessions, and answer that one."""
        return self.sessions.wait_for(number)

    def health(self) -> Health:
        return Health(
            ready=True,
            provider=ProviderHealth(name="fake", ready=True, plays=[Verb.SPEAK, Verb.CLEAR], video=PICTURE),
            looks={name: LookHealth(ready=ready) for name, ready in self.looks.items()},
            sessions=SessionsHealth(open=sum(not session.is_closed() for session in self.sessions), limit=1),
            card_memory_mb=0,
        )

    def _answer(self, connection: ServerConnection, request: Request) -> Response | None:
        path = urlsplit(request.path)
        if path.path == HEALTH_PATH:
            self.health_requests += 1
            self.health_asked.set()
            assert self.health_open.wait(WAIT_SECONDS), "a health read was held for good"
            return self._respond(connection, self.health_status, self.health().model_dump_json())
        assert path.path == SESSIONS_PATH
        self.upgrades += 1
        self.upgrade_asked.set()
        assert self.opening.wait(WAIT_SECONDS), "an upgrade was held for good"
        if self.refusal is None:
            return None
        return self._respond(connection, self.refusal.status, self.refusal.body)

    @staticmethod
    def _respond(connection: ServerConnection, status: HTTPStatus, body: str) -> Response:
        response = connection.respond(status, body)
        response.headers["Content-Type"] = "application/json"
        return response

    def _converse(self, connection: ServerConnection) -> None:
        look = parse_qs(urlsplit(connection.request.path).query)[LOOK_PARAM][0]
        session = Session(dict(connection.request.headers.items()), look)
        self.sessions.arrive(session)
        try:
            connection.send(
                SessionCreated(session=SessionInfo(id="session-one", look=look, video=PICTURE)).model_dump_json()
            )
            self._serve(connection, session)
        except ConnectionClosed:
            pass
        finally:
            session.close_code = connection.close_code
            session.closed.set()

    def _serve(self, connection: ServerConnection, session: Session) -> None:
        answered = False
        while True:
            try:
                outgoing = session._outgoing.get_nowait()  # noqa: SLF001 - the fake's own session
            except queue.Empty:
                outgoing = ""
            if outgoing is None:
                connection.close()
                return
            if outgoing != "":
                connection.send(outgoing if isinstance(outgoing, str | bytes) else outgoing.model_dump_json())
            try:
                message = connection.recv(timeout=POLL_SECONDS)
            except TimeoutError:
                continue
            session.received(message if isinstance(message, str) else message.decode())
            if self.updates and not answered:
                answered = True
                connection.send(self.update_reply.model_dump_json())
