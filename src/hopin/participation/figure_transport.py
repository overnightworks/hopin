"""The figure transport over urllib3: one streamed GET of exactly the URL it is given, with no redirect followed, ended
when the time left runs out."""

import http.client
import socket
import threading
import time
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING, Final

import urllib3
from urllib3.connection import HTTPSConnection

from hopin.participation.figures import HTTPS_SCHEME, FigureAnswer, FigureFetchError
from hopin.participation.vocabulary import FigureRefusal
from hopin.web.turn_relay import urllib3_unlogged

if TYPE_CHECKING:
    from collections.abc import Iterator

CHUNK_BYTES: Final = 64 * 1024
# A connection used directly raises below urllib3's own wrapping.
FETCH_ERRORS: Final = (urllib3.exceptions.HTTPError, http.client.HTTPException, OSError)


class _Deadline:
    """The time left for one fetch. A phase is bound as follows: urllib3's timeout bounds the TCP connect; the
    connection hands the TLS handshake the time still left; the timer ends the sockets of the request and the answer,
    since urllib3's timeouts bound each socket operation, not the whole answer, and an origin dripping its headers or
    body would outrun them."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self._ends_at = time.monotonic() + seconds
        self._sock: socket.socket | None = None
        self._passed = threading.Event()
        self._timer = threading.Timer(seconds, self._end)
        self._timer.start()

    @property
    def passed(self) -> bool:
        return self._passed.is_set()

    def seconds_left(self) -> float:
        return self._ends_at - time.monotonic()

    def watch(self, sock: socket.socket | None) -> None:
        """End this socket at the deadline too, the connected one the response reads from."""
        self._sock = sock

    def raise_if_passed(self) -> None:
        if self.passed:
            raise FigureFetchError(FigureRefusal.PAST_DEADLINE)

    def cancel(self) -> None:
        """Stop the timer and wait for it, so it never shuts a socket the connection is closing."""
        self._timer.cancel()
        self._timer.join()

    def _end(self) -> None:
        self._passed.set()
        if self._sock is not None:
            # A shutdown wakes a read blocked in another thread, TLS or not; a socket the origin already ended has
            # nothing left to end.
            with suppress(OSError):
                socket.socket.shutdown(self._sock, socket.SHUT_RDWR)


class _DeadlineConnection(HTTPSConnection):
    """A connection whose TLS handshake gets the time still left when the TCP connect is made, not a timeout of its
    own: the handshake runs on a socket the timer cannot reach."""

    def __init__(self, host: str, port: int | None, deadline: _Deadline) -> None:
        super().__init__(host, port, timeout=deadline.seconds)
        self._deadline = deadline

    def _new_conn(self) -> socket.socket:
        sock = super()._new_conn()
        seconds_left = self._deadline.seconds_left()
        if seconds_left <= 0:
            sock.close()
            raise FigureFetchError(FigureRefusal.PAST_DEADLINE)
        sock.settimeout(seconds_left)
        return sock


@contextmanager
def get_figure_over_https(url: str, timeout_seconds: float) -> Iterator[FigureAnswer]:
    try:
        parts = urllib3.util.parse_url(url)
    except urllib3.exceptions.LocationParseError:
        raise FigureFetchError(FigureRefusal.MALFORMED_URL) from None
    if parts.scheme != HTTPS_SCHEME:
        raise FigureFetchError(FigureRefusal.MALFORMED_URL)
    # urllib3 logs the request's path; inside this block its loggers drop every record of this thread.
    with urllib3_unlogged():
        deadline = _Deadline(timeout_seconds)
        connection = _DeadlineConnection(str(parts.host), parts.port, deadline)
        try:
            response = _response(connection, parts.request_uri, deadline)
            yield FigureAnswer(
                response.status,
                response.headers.get("Location"),
                response.length_remaining,
                _chunks(response, deadline),
            )
        finally:
            deadline.cancel()
            connection.close()


def _response(connection: HTTPSConnection, request_uri: str, deadline: _Deadline) -> urllib3.BaseHTTPResponse:
    try:
        # Watched before the check, so a time left that runs out on either side of it ends the fetch.
        connection.connect()
        deadline.watch(connection.sock)
        deadline.raise_if_passed()
        # The default would read the whole body into memory before the size cap can stop it.
        connection.request("GET", request_uri, preload_content=False)  # NOSONAR(S930) urllib3's request takes it
        response = connection.getresponse()
    except FETCH_ERRORS as error:
        raise _fetch_error(error, deadline) from None
    # Headers cut off at the deadline read as complete ones.
    deadline.raise_if_passed()
    return response


def _chunks(response: urllib3.BaseHTTPResponse, deadline: _Deadline) -> Iterator[bytes]:
    try:
        yield from response.stream(CHUNK_BYTES, decode_content=False)
    except FETCH_ERRORS as error:
        raise _fetch_error(error, deadline) from None
    # A body without a length, cut off at the deadline, reads as a complete one.
    deadline.raise_if_passed()


def _fetch_error(error: Exception, deadline: _Deadline) -> FigureFetchError:
    # A refused connection is a NewConnectionError, which urllib3 derives from its connect timeout.
    timed_out = isinstance(error, TimeoutError | urllib3.exceptions.TimeoutError) and not isinstance(
        error, urllib3.exceptions.NewConnectionError
    )
    return FigureFetchError(FigureRefusal.PAST_DEADLINE if deadline.passed or timed_out else FigureRefusal.UNREACHABLE)
