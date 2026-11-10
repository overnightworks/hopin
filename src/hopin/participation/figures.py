"""An agent's figure: the allowlist its URL meets at accept, the one fetch that verifies its bytes, and the store the
route serves them from.

Apart from the transport port and the store's own two workers, nothing here does I/O; the clock comes in as a
parameter.
"""

import asyncio
import hashlib
import string
import struct
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol
from urllib.parse import urljoin, urlsplit

import structlog

from hopin.participation.vocabulary import (
    FIGURE_CACHE_BYTES,
    FIGURE_FETCH_SECONDS,
    FIGURE_FETCH_WORKERS,
    FIGURE_MAX_BYTES,
    FIGURE_PATH,
    FIGURE_REDIRECTS,
    FigureRefusal,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Sequence
    from contextlib import AbstractContextManager

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

HTTPS_SCHEME: Final = "https"
PATH_CHARACTERS: Final = frozenset(string.ascii_letters + string.digits + "-._~/")
REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})
GLB_HEADER: Final = struct.Struct("<4sII")
GLB_MAGIC: Final = b"glTF"
GLB_VERSION: Final = 2
FETCH_THREAD_PREFIX: Final = "participation-figure"


@dataclass(frozen=True)
class Figure:
    """An agent's figure as its accept names it: the URL and the sha256 its bytes must have."""

    url: str
    sha256: str


@dataclass(frozen=True)
class FigureSource:
    """An allowed source: a figure's URL is on this host, with no port, and its path starts with this path."""

    host: str
    path: str


@dataclass(frozen=True)
class NamedFigure:
    """A URL a joined agent names for a digest, and the room it is joined in."""

    room_id: str
    url: str


@dataclass(frozen=True)
class FigureAnswer:
    """The answer to one GET: its status, its `Location` and `Content-Length` when sent, and its body as it streams."""

    status: int
    location: str | None
    content_length: int | None
    body: Iterable[bytes]


class FigureFetchError(Exception):
    """The transport got no answer, or the body broke off."""

    def __init__(self, refusal: FigureRefusal) -> None:
        super().__init__(refusal.value)
        self.refusal = refusal


class FigureTransport(Protocol):
    def __call__(self, url: str, timeout_seconds: float) -> AbstractContextManager[FigureAnswer]:
        """One GET of exactly `url`, without credential, cookie, retry or redirect; the answer is read within the
        block. Raises `FigureFetchError`."""


def figure_path(sha256: str) -> str:
    """Where the browser reads the figure of this digest."""
    return f"{FIGURE_PATH}/{sha256}"


def path_refusal(path: str) -> FigureRefusal | None:
    """Why a path breaks the path rule, or nothing: no `%`, only ASCII letters, digits and `-._~/`, and no empty,
    `.` or `..` segment after its leading `/`."""
    if "%" in path:
        return FigureRefusal.PERCENT
    if not path.isascii():
        return FigureRefusal.NON_ASCII
    if not PATH_CHARACTERS.issuperset(path):
        return FigureRefusal.DISALLOWED_CHARACTER
    segments = path.split("/")[1:]
    if "" in segments:
        return FigureRefusal.EMPTY_SEGMENT
    if "." in segments:
        return FigureRefusal.DOT_SEGMENT
    if ".." in segments:
        return FigureRefusal.DOT_DOT_SEGMENT
    return None


def figure_refusal(sources: Collection[FigureSource], url: str) -> FigureRefusal | None:
    """Why the figure's URL is outside every allowed source, or nothing when one allows it: not https, a query, a
    fragment, a port, an upper-case host, a host or path no source allows, and a string that parsing changes (it strips
    whitespace and control characters) are all refused here."""
    if not sources:
        return FigureRefusal.NO_SOURCE
    parts = urlsplit(url)
    if parts.geturl() != url:
        return FigureRefusal.ALTERED_BY_PARSING
    if parts.scheme != HTTPS_SCHEME:
        return FigureRefusal.NOT_HTTPS
    if parts.query:
        return FigureRefusal.QUERY
    if parts.fragment:
        return FigureRefusal.FRAGMENT
    # The netloc, not `hostname`, which lowercases the host and drops an empty port.
    if ":" in parts.netloc:
        return FigureRefusal.PORT
    if parts.netloc != parts.netloc.lower():
        return FigureRefusal.UPPERCASE_HOST
    paths = [source.path for source in sources if source.host == parts.netloc]
    if not paths:
        return FigureRefusal.OTHER_HOST
    refusal = path_refusal(parts.path)
    if refusal is not None:
        return refusal
    if not parts.path.startswith(tuple(paths)):
        return FigureRefusal.OUTSIDE_PREFIX
    return None


@dataclass(frozen=True)
class _Redirect:
    location: str


def fetch_figure(
    transport: FigureTransport, figure: Figure, *, monotonic: Callable[[], float]
) -> bytes | FigureRefusal:
    """The figure's verified bytes, or why there are none. One deadline spans the whole fetch, and at most one redirect
    to https is followed."""
    deadline = monotonic() + FIGURE_FETCH_SECONDS
    url, redirects = figure.url, 0
    while True:
        try:
            outcome = _get_once(transport, url, figure.sha256, deadline, monotonic)
        except FigureFetchError as error:
            return error.refusal
        if not isinstance(outcome, _Redirect):
            return outcome
        if redirects == FIGURE_REDIRECTS:
            return FigureRefusal.SECOND_REDIRECT
        try:
            url, redirects = urljoin(url, outcome.location), redirects + 1
        except ValueError:
            return FigureRefusal.MALFORMED_URL
        if urlsplit(url).scheme != HTTPS_SCHEME:
            return FigureRefusal.REDIRECT_NOT_HTTPS


def _get_once(
    transport: FigureTransport, url: str, sha256: str, deadline: float, monotonic: Callable[[], float]
) -> bytes | FigureRefusal | _Redirect:
    remaining = deadline - monotonic()
    if remaining <= 0:
        return FigureRefusal.PAST_DEADLINE
    with transport(url, remaining) as answer:
        if answer.status in REDIRECT_STATUSES and answer.location is not None:
            return _Redirect(answer.location)
        if answer.status != 200:
            return FigureRefusal.NOT_OK
        if answer.content_length is not None and answer.content_length > FIGURE_MAX_BYTES:
            return FigureRefusal.TOO_LARGE
        body = _read_within(answer.body, deadline, monotonic)
    return body if isinstance(body, FigureRefusal) else _verified(body, sha256)


def _read_within(chunks: Iterable[bytes], deadline: float, monotonic: Callable[[], float]) -> bytes | FigureRefusal:
    body = bytearray()
    for chunk in chunks:
        if monotonic() >= deadline:
            return FigureRefusal.PAST_DEADLINE
        body += chunk
        if len(body) > FIGURE_MAX_BYTES:
            return FigureRefusal.TOO_LARGE
    return bytes(body)


def _verified(body: bytes, sha256: str) -> bytes | FigureRefusal:
    if hashlib.sha256(body).hexdigest() != sha256:
        return FigureRefusal.MISMATCH
    if len(body) < GLB_HEADER.size or GLB_HEADER.unpack_from(body) != (GLB_MAGIC, GLB_VERSION, len(body)):
        return FigureRefusal.BAD_HEADER
    return body


class FigureStore:
    """The verified figures by digest, least recently used first, within `FIGURE_CACHE_BYTES`. A digest has one fetch
    at a time, and fetches run on two workers of their own, so stalled ones cannot starve the loop's default
    executor."""

    def __init__(self, fetch: Callable[[Figure], bytes | FigureRefusal]) -> None:
        self._fetch_one = fetch
        self._executor = ThreadPoolExecutor(max_workers=FIGURE_FETCH_WORKERS, thread_name_prefix=FETCH_THREAD_PREFIX)
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._fetches: dict[str, asyncio.Task[bytes | None]] = {}

    async def bytes_of(self, sha256: str, named: Sequence[NamedFigure]) -> bytes | None:
        """The verified bytes of the digest, fetched from the named URLs one after another until one verifies; nothing
        when none does, and the next read fetches again."""
        cached = self._cache.get(sha256)
        if cached is not None:
            self._cache.move_to_end(sha256)
            return cached
        fetch = self._fetches.get(sha256)
        if fetch is None:
            fetch = asyncio.create_task(self._fetch(sha256, tuple(named)))
            self._fetches[sha256] = fetch
        # A reader that goes away leaves the fetch running for the others.
        return await asyncio.shield(fetch)

    def shut_down(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def _fetch(self, sha256: str, named: tuple[NamedFigure, ...]) -> bytes | None:
        loop = asyncio.get_running_loop()
        try:
            for figure in named:
                outcome = await loop.run_in_executor(self._executor, self._fetch_one, Figure(figure.url, sha256))
                if isinstance(outcome, bytes):
                    self._keep(sha256, outcome)
                    return outcome
                await logger.ainfo("agent_figure_fetch_failed", room_id=figure.room_id, reason=outcome.value)
            return None
        finally:
            del self._fetches[sha256]

    def _keep(self, sha256: str, body: bytes) -> None:
        self._cache[sha256] = body
        while sum(map(len, self._cache.values())) > FIGURE_CACHE_BYTES:
            self._cache.popitem(last=False)
