"""An agent's video session on the video service: the open that follows an agent's accept and the session's life.

The open reads the service's health, exchanges the accept's token for the video audience alone, asks HopIn's own
workload token and a TURN credential, upgrades `WS /v1/sessions` (WIRE-2, WIRE-3) and sends `session.update` with the
relay's servers alone under the `relay` policy (WIRE-4). The session is open once the service answers `session.updated`,
within `VIDEO_SESSION_OPEN_SECONDS` of the upgrade, and participation's look owner keeps it; from then on a task of
its own reads the service's events until an `error` or a close ends the session (WIRE-7) or the owner closes it.

No token, ICE credential or frame reaches a log line: the connection's logger drops what it logs at DEBUG, and the
steps' lines carry closed codes."""

import asyncio
import logging
from functools import partial
from typing import TYPE_CHECKING, Final
from urllib.parse import urlencode

from pydantic import ValidationError
from video_client.health import fetch_health
from video_client.session_wire import (
    ErrorEvent,
    IceConfiguration,
    ServerEvent,
    ServerEventError,
    SessionCreated,
    SessionUpdate,
    SessionUpdated,
    parse_server_event,
)
from video_client.session_wire import (
    IceServer as WireIceServer,
)
from video_client.wire import LOOK_PARAM, SESSIONS_PATH, Health, Refusal, RefusalCode, caller_headers
from webauth.oidc import OidcRefusal
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

from hopin.agent.hearing_token import exchange_step
from hopin.agent.workload_token import workload_token
from hopin.participation.vocabulary import VideoFailure
from hopin.web.ice_servers import TurnRelayFailure, issued_relay_servers

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from logline import StepTimer
    from starlette.requests import HTTPConnection
    from webauth.oidc import OidcRelyingParty

    from hopin.agent.tools import AcceptedVideoLooks
    from hopin.agent.video_settings import VideoService
    from hopin.participation.feature import Participation
    from hopin.participation.participants import DialedAgent, Person
    from hopin.signaling.scheduler import Scheduler
    from hopin.web.ice_servers import IceServer, TurnRelay

VIDEO_SESSION_OPEN_SECONDS: Final = 10
HEALTH_DEADLINE_SECONDS: Final = 5
HEALTH_MAX_BYTES: Final = 64 * 1024
SOCKET_LOGGER_NAME: Final = f"{__name__}.socket"
# The refusals of an upgrade that WIRE-2 and WIRE-3 name; any other refusal ends the attempt as `refused`.
_UPGRADE_REFUSALS: Final = {
    RefusalCode.UNAUTHORIZED: VideoFailure.UNAUTHORIZED,
    RefusalCode.FORBIDDEN: VideoFailure.FORBIDDEN,
    RefusalCode.INVALID_REQUEST: VideoFailure.INVALID_REQUEST,
    RefusalCode.UNAVAILABLE: VideoFailure.UNAVAILABLE,
    RefusalCode.BUSY: VideoFailure.BUSY,
}


def _above_debug(record: logging.LogRecord) -> bool:
    return record.levelno > logging.DEBUG


# The WebSocket client logs each frame and the upgrade's headers at DEBUG, which carry the tokens and the TURN
# credential; this logger, which every connection of a session logs on, drops them whatever the level.
_SOCKET: Final = logging.getLogger(SOCKET_LOGGER_NAME)
_SOCKET.addFilter(_above_debug)


class _EndedError(Exception):
    """The service ended the session, or broke the wire."""

    def __init__(self, failure: VideoFailure) -> None:
        super().__init__(failure)
        self.failure = failure


async def _next_event(connection: ClientConnection) -> ServerEvent:
    try:
        message = await connection.recv()
    except ConnectionClosed:
        raise _EndedError(VideoFailure.CLOSED) from None
    if isinstance(message, bytes):
        raise _EndedError(VideoFailure.MALFORMED)
    try:
        event = parse_server_event(message)
    except ServerEventError:
        raise _EndedError(VideoFailure.MALFORMED) from None
    if isinstance(event, ErrorEvent):
        raise _EndedError(VideoFailure.ERROR)
    return event


async def _expect[Event: ServerEvent](connection: ClientConnection, kind: type[Event]) -> None:
    if not isinstance(await _next_event(connection), kind):
        raise _EndedError(VideoFailure.MALFORMED)


def _upgrade_failure(refused: InvalidStatus) -> VideoFailure:
    try:
        code = Refusal.model_validate_json(refused.response.body or b"").error.code
    except ValidationError:
        return VideoFailure.REFUSED
    return _UPGRADE_REFUSALS.get(code, VideoFailure.REFUSED)


def _session_update(servers: list[IceServer]) -> str:
    relay = IceConfiguration(
        ice_servers=[
            WireIceServer(urls=server.urls, username=server.username, credential=server.credential)
            for server in servers
        ],
        ice_transport_policy="relay",
    )
    return SessionUpdate(type="session.update", session=relay).model_dump_json(exclude_none=True)


async def _opened(
    origin: str, look: str, headers: dict[str, str], servers: list[IceServer]
) -> ClientConnection | VideoFailure:
    """The upgraded connection once the service answered `session.updated`, or why the session did not open."""
    url = f"ws{origin.removeprefix('http')}{SESSIONS_PATH}?{urlencode({LOOK_PARAM: look})}"
    connection: ClientConnection | None = None
    try:
        async with asyncio.timeout(VIDEO_SESSION_OPEN_SECONDS):
            connection = await connect(url, additional_headers=headers, logger=_SOCKET, open_timeout=None)
            await _expect(connection, SessionCreated)
            await connection.send(_session_update(servers))
            await _expect(connection, SessionUpdated)
    except TimeoutError:
        failure = VideoFailure.PAST_DEADLINE
    except InvalidStatus as refused:
        failure = _upgrade_failure(refused)
    except _EndedError as ended:
        failure = ended.failure
    except OSError, WebSocketException:
        failure = VideoFailure.UNREACHABLE
    else:
        return connection
    if connection is not None:
        await connection.close()
    return failure


class OpenSession:
    """A session the service answered `session.updated` for. A task of its own reads its events until the session
    ends, then reports why, unless `close` ended it."""

    def __init__(
        self, connection: ClientConnection, ended: Callable[[VideoFailure], None], tasks: set[asyncio.Task[None]]
    ) -> None:
        self._connection = connection
        self._ended = ended
        self._tasks = tasks
        self._closed = False
        self._start(self._watch())

    def close(self) -> None:
        self._closed = True
        self._start(self._connection.close())

    def _start(self, work: Coroutine[None, None, None]) -> None:
        task = asyncio.get_running_loop().create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _watch(self) -> None:
        try:
            failure = await self._until_ended()
        finally:
            await self._connection.close()
        if not self._closed:
            self._ended(failure)

    async def _until_ended(self) -> VideoFailure:
        try:
            while True:
                await _next_event(self._connection)
        except _EndedError as ended:
            return ended.failure


class VideoOpening:
    """Opens an agent's video session after its accept, on the scheduler, and never before the accept has answered."""

    def __init__(
        self,
        service: VideoService,
        *,
        relying_party: OidcRelyingParty,
        turn_relay: TurnRelay,
        participation: Callable[[], Participation],
        scheduler: Scheduler,
        wall_clock: Callable[[], float],
    ) -> None:
        self._service = service
        self._relying_party = relying_party
        self._turn_relay = turn_relay
        self._participation = participation
        self._scheduler = scheduler
        self._wall_clock = wall_clock
        self._sessions: set[asyncio.Task[None]] = set()

    def after_accept(self, person: Person, looks: AcceptedVideoLooks, connection: HTTPConnection) -> None:
        """Start the open for the agent the accept joined when it lists a video look ahead of its figure look. The
        connection is the accept's own request, whose token the exchange presents once the accept has answered."""
        if not looks.names:
            return
        agent = self._participation().video_looks.joined(person, looks.room_id)
        if agent is not None:
            self._scheduler.call_later(0, partial(self._open, agent, looks.names, connection))

    async def _open(self, agent: DialedAgent, names: tuple[str, ...], connection: HTTPConnection) -> None:
        look = await self._first_ready(names)
        if look is None:
            return
        video_looks = self._participation().video_looks
        attempt = video_looks.attempt(agent)
        if isinstance(look, VideoFailure):
            video_looks.refused(agent, attempt, look)
            return
        failure = await self._opened(agent, look, connection, attempt)
        if failure is not None:
            video_looks.refused(agent, attempt, failure)

    async def _first_ready(self, names: tuple[str, ...]) -> str | VideoFailure | None:
        """The first look of `names` the service's health offers ready, none when it offers none, or why the health
        could not be read; a look it does not offer or does not offer ready is skipped."""
        health = await asyncio.to_thread(
            fetch_health, self._service.origin, deadline_seconds=HEALTH_DEADLINE_SECONDS, cap_bytes=HEALTH_MAX_BYTES
        )
        if not isinstance(health, Health):
            return VideoFailure.HEALTH_UNREAD
        return next((name for name in names if name in health.looks and health.looks[name].ready), None)

    async def _opened(
        self, agent: DialedAgent, look: str, connection: HTTPConnection, attempt: StepTimer
    ) -> VideoFailure | None:
        scope = self._service.audience_scope
        exchanged = await exchange_step(connection, (scope,), self._wall_clock)
        if isinstance(exchanged, OidcRefusal):
            return VideoFailure.TOKEN_EXCHANGE_REFUSED
        workload = await workload_token(self._relying_party, scope)
        if isinstance(workload, OidcRefusal):
            return VideoFailure.WORKLOAD_TOKEN_REFUSED
        servers = await asyncio.to_thread(issued_relay_servers, self._turn_relay)
        if isinstance(servers, TurnRelayFailure):
            return VideoFailure.TURN_CREDENTIAL_UNAVAILABLE
        headers = caller_headers(
            workload_token=workload.get_secret_value(), person_token=exchanged.access_token.get_secret_value()
        )
        opened = await _opened(self._service.origin, look, headers, servers)
        if isinstance(opened, VideoFailure):
            return opened
        video_looks = self._participation().video_looks
        session = OpenSession(opened, partial(video_looks.refused, agent, attempt), self._sessions)
        video_looks.opened(agent, session, attempt)
        return None
