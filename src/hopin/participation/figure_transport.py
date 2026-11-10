"""The figure transport over urllib3: one streamed GET of exactly the URL it is given, with no redirect followed, ended
when the time left runs out."""

import http.client
import socket
import threading
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING, Final

import urllib3
from urllib3.connection import HTTPConnection, HTTPSConnection

from hopin.participation.figures import FigureAnswer, FigureFetchError
from hopin.participation.vocabulary import FigureRefusal
from hopin.web.turn_relay import urllib3_unlogged

if TYPE_CHECKING:
    from collections.abc import Iterator

CHUNK_BYTES: Final = 64 * 1024
CONNECTIONS_BY_SCHEME: Final[dict[str | None, type[HTTPConnection]]] = {
    "http": HTTPConnection,
    "https": HTTPSConnection,
}
# A connection used directly raises below urllib3's own wrapping.
FETCH_ERRORS: Final = (urllib3.exceptions.HTTPError, http.client.HTTPException, OSError)


class _Deadline:
    """Ends the connection when the time left runs out: urllib3's timeouts bound each socket operation, not the whole
    answer, so an origin dripping its headers or body would outrun them."""

    def __init__(self, connection: HTTPConnection, seconds: float) -> None:
        self._connection = connection
        self._sock: socket.socket | None = None
        self._passed = threading.Event()
        self._timer = threading.Timer(seconds, self._end)
        self._timer.start()

    @property
    def passed(self) -> bool:
        return self._passed.is_set()

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
        # The connection holds its plain socket while the TLS handshake runs, and lets go of it once a response that
        # closes the connection takes it over.
        for sock in filter(None, (self._connection.sock, self._sock)):
            # The plain socket's shutdown wakes a read blocked in another thread, TLS or not; a socket the origin
            # already ended has nothing left to end.
            with suppress(OSError):
                socket.socket.shutdown(sock, socket.SHUT_RDWR)


@contextmanager
def get_figure_over_https(url: str, timeout_seconds: float) -> Iterator[FigureAnswer]:
    try:
        parts = urllib3.util.parse_url(url)
    except urllib3.exceptions.LocationParseError:
        raise FigureFetchError(FigureRefusal.MALFORMED_URL) from None
    connection = CONNECTIONS_BY_SCHEME[parts.scheme](str(parts.host), parts.port, timeout=timeout_seconds)
    # urllib3 logs the request's path; inside this block its loggers drop every record of this thread.
    with urllib3_unlogged():
        deadline = _Deadline(connection, timeout_seconds)
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


def _response(connection: HTTPConnection, request_uri: str, deadline: _Deadline) -> urllib3.BaseHTTPResponse:
    try:
        # Watched before the check, so a time left that runs out on either side of it ends the fetch.
        connection.connect()
        deadline.watch(connection.sock)
        deadline.raise_if_passed()
        connection.request("GET", request_uri, preload_content=False, decode_content=False)
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
