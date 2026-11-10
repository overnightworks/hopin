"""Figures as the tests arrange them: glTF binary files, an allowed source, and a figure origin behind the transport
port that answers each URL from a script."""

import hashlib
import struct
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field

from hopin.participation.figures import FigureAnswer, FigureFetchError
from hopin.participation.vocabulary import FigureRefusal

SOURCE_HOST = "figures.example.test"
SOURCE_PREFIX = f"https://{SOURCE_HOST}/personas/releases/download/"
GLB_HEADER = struct.Struct("<4sII")
WAIT_SECONDS = 10


def glb(payload: bytes = b"\x00" * 16) -> bytes:
    """A glTF binary file: its 12-byte header with the total length, then the payload."""
    return GLB_HEADER.pack(b"glTF", 2, GLB_HEADER.size + len(payload)) + payload


def digest_of(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def figure_url(name: str = "mozart.glb") -> str:
    return f"{SOURCE_PREFIX}v0.1.0/{name}"


class FakeClock:
    """A monotonic clock the test moves."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@dataclass
class Answer:
    """What the origin answers one GET: a status, the headers it sends, and the body in chunks. `on_get` runs as the
    GET arrives and `on_chunk` as each chunk is read; `broken` raises the transport's failure in place of an answer."""

    status: int = 200
    body: bytes = b""
    chunk_bytes: int = 1 << 20
    location: str | None = None
    content_length: int | None = None
    on_get: object = None
    on_chunk: object = None
    broken: FigureRefusal | None = None

    def chunks(self):
        for start in range(0, len(self.body), self.chunk_bytes):
            if self.on_chunk is not None:
                self.on_chunk()
            yield self.body[start : start + self.chunk_bytes]


def ok(body: bytes, **options) -> Answer:
    return Answer(200, body, content_length=len(body), **options)


def redirect(location: str) -> Answer:
    return Answer(302, location=location)


@dataclass
class FigureOrigin:
    """The far side of the transport port: answers each URL from its script and records each GET. `held` URLs wait for
    `release` once `arrived` is set."""

    answers: dict[str, Answer] = field(default_factory=dict)
    requested: list[str] = field(default_factory=list)
    timeouts: list[float] = field(default_factory=list)
    held: dict[str, tuple[threading.Event, threading.Event]] = field(default_factory=dict)

    def serve(self, url: str, answer: Answer) -> str:
        self.answers[url] = answer
        return url

    def hold(self, url: str) -> tuple[threading.Event, threading.Event]:
        """Hold the URL's answer: the first event is set when the GET arrives, the second releases it."""
        self.held[url] = (threading.Event(), threading.Event())
        return self.held[url]

    @contextmanager
    def __call__(self, url: str, timeout_seconds: float):
        self.requested.append(url)
        self.timeouts.append(timeout_seconds)
        if url in self.held:
            arrived, release = self.held[url]
            arrived.set()
            assert release.wait(WAIT_SECONDS)
        answer = self.answers.get(url)
        if answer is not None and answer.on_get is not None:
            answer.on_get()
        if answer is None or answer.broken is not None:
            raise FigureFetchError(FigureRefusal.UNREACHABLE if answer is None else answer.broken)
        yield FigureAnswer(answer.status, answer.location, answer.content_length, answer.chunks())
