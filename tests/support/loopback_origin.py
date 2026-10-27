"""A loopback HTTP origin for transport tests: real sockets, answers from a script, held until released."""

import socket
import threading
from collections import deque
from dataclasses import dataclass, field

LOCALHOST = "127.0.0.1"
ARRIVAL_CAP_SECONDS = 10


def http_response(
    status_line: str, body: bytes, *, extra_header: bytes = b"", declared_length: int | None = None
) -> bytes:
    length = len(body) if declared_length is None else declared_length
    head = f"HTTP/1.1 {status_line}\r\nContent-Length: {length}\r\nConnection: close\r\n".encode()
    return head + extra_header + b"\r\n" + body


def streamed_head(content_type: str) -> bytes:
    """The head of an answer whose body runs until the origin closes the connection."""
    return f"HTTP/1.1 200 OK\r\nContent-Type: {content_type}\r\nConnection: close\r\n\r\n".encode()


@dataclass(eq=False)
class HeldStream:
    """One streamed answer: `first` goes out at once, `rest` once released, then the origin closes.

    `peer_closed` is set when the client closed the connection, which the origin notices while it holds.
    """

    first: bytes
    rest: bytes
    started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    peer_closed: threading.Event = field(default_factory=threading.Event)


@dataclass
class Origin:
    """A loopback HTTP origin: it answers each path from a script and can hold an answer until released.

    A path served with streams answers its requests with them in arrival order, so two runs can be held at once.
    """

    requests: list[tuple[str, str, dict[str, str], bytes]] = field(default_factory=list)
    received: dict[str, threading.Event] = field(default_factory=dict)
    release: dict[str, threading.Event] = field(default_factory=dict)
    scripts: dict[str, bytes] = field(default_factory=dict)
    streams: dict[str, deque[HeldStream]] = field(default_factory=dict)
    held_streams: list[HeldStream] = field(default_factory=list)
    listener: socket.socket = field(default_factory=socket.socket)
    threads: list[threading.Thread] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.listener.bind((LOCALHOST, 0))
        self.listener.listen()
        self.port = self.listener.getsockname()[1]
        self.base_url = f"http://{LOCALHOST}:{self.port}"
        self._accepting = threading.Thread(target=self._accept)
        self._accepting.start()

    def serve(self, path: str, answer: bytes, *, held: bool = False) -> str:
        self.scripts[path] = answer
        self.received[path] = threading.Event()
        self.release[path] = threading.Event()
        if not held:
            self.release[path].set()
        return f"{self.base_url}{path}"

    def serve_streams(self, path: str, *streams: HeldStream) -> str:
        self.streams.setdefault(path, deque()).extend(streams)
        self.held_streams.extend(streams)
        return f"{self.base_url}{path}"

    def paths_requested(self) -> list[str]:
        return [path for _, path, _, _ in self.requests]

    def close(self) -> None:
        for release in self.release.values():
            release.set()
        for stream in self.held_streams:
            stream.release.set()
        self.listener.shutdown(socket.SHUT_RDWR)
        self.listener.close()
        self._accepting.join()
        for thread in self.threads:
            thread.join()

    def _accept(self) -> None:
        while True:
            try:
                connection, _ = self.listener.accept()
            except OSError:
                return
            self._start(self._answer, connection)

    def _start(self, target, *args) -> None:
        thread = threading.Thread(target=target, args=args)
        self.threads.append(thread)
        thread.start()

    def _answer(self, connection: socket.socket) -> None:
        with connection:
            request = _read_request(connection)
            if request is None:
                return
            self.requests.append(request)
            _, path, _, _ = request
            if self.streams.get(path):
                self._stream(connection, self.streams[path].popleft())
                return
            if path not in self.scripts:
                connection.sendall(http_response("404 Not Found", b""))
                return
            self.received[path].set()
            self.release[path].wait(ARRIVAL_CAP_SECONDS)
            try:
                connection.sendall(self.scripts[path])
            except OSError:
                return

    def _stream(self, connection: socket.socket, stream: HeldStream) -> None:
        connection.sendall(stream.first)
        self._start(_watch_for_close, connection, stream.peer_closed)
        stream.started.set()
        stream.release.wait(ARRIVAL_CAP_SECONDS)
        try:
            connection.sendall(stream.rest)
            connection.shutdown(socket.SHUT_WR)
        except OSError:
            return
        stream.peer_closed.wait(ARRIVAL_CAP_SECONDS)


def _read_request(connection: socket.socket) -> tuple[str, str, dict[str, str], bytes] | None:
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = connection.recv(4096)
        if not chunk:
            return None
        head += chunk
    head, _, body = head.partition(b"\r\n\r\n")
    method, path, _ = head.split(b"\r\n")[0].decode().split(" ")
    headers = dict(line.decode().split(": ", 1) for line in head.split(b"\r\n")[1:])
    content_length = int(next((value for name, value in headers.items() if name.lower() == "content-length"), 0))
    while len(body) < content_length:
        body += connection.recv(4096)
    return method, path, headers, body


def _watch_for_close(connection: socket.socket, peer_closed: threading.Event) -> None:
    try:
        while connection.recv(4096):
            pass
    except OSError:
        pass
    peer_closed.set()
