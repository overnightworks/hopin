"""The figure transport over urllib3: one streamed GET of exactly the URL it is given, with no redirect followed."""

from contextlib import contextmanager
from typing import TYPE_CHECKING, Final

import urllib3

from hopin.participation.figures import FigureAnswer, FigureFetchError
from hopin.participation.vocabulary import FigureRefusal
from hopin.web.turn_relay import _urllib3_unlogged

if TYPE_CHECKING:
    from collections.abc import Iterator

CHUNK_BYTES: Final = 64 * 1024


@contextmanager
def get_figure_over_https(url: str, timeout_seconds: float) -> Iterator[FigureAnswer]:
    # urllib3 logs the request's path; inside this block its loggers drop every record of this thread.
    with _urllib3_unlogged(), urllib3.PoolManager() as pool:
        try:
            response = pool.request(
                "GET",
                url,
                timeout=urllib3.Timeout(total=timeout_seconds),
                retries=False,
                redirect=False,
                preload_content=False,
                decode_content=False,
            )
        except urllib3.exceptions.HTTPError as error:
            raise _fetch_error(error) from None
        try:
            yield FigureAnswer(
                response.status, response.headers.get("Location"), response.length_remaining, _chunks(response)
            )
        finally:
            response.release_conn()


def _chunks(response: urllib3.BaseHTTPResponse) -> Iterator[bytes]:
    try:
        yield from response.stream(CHUNK_BYTES, decode_content=False)
    except urllib3.exceptions.HTTPError as error:
        raise _fetch_error(error) from None


def _fetch_error(error: urllib3.exceptions.HTTPError) -> FigureFetchError:
    # A refused connection is a NewConnectionError, which urllib3 derives from its connect timeout.
    if isinstance(error, urllib3.exceptions.TimeoutError) and not isinstance(
        error, urllib3.exceptions.NewConnectionError
    ):
        return FigureFetchError(FigureRefusal.PAST_DEADLINE)
    return FigureFetchError(FigureRefusal.UNREACHABLE)
