"""A TCP forwarder in front of the Redis a test runs against; closing it is that Redis going down for its clients."""

import contextlib
import socket
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Callable

LOCALHOST = "127.0.0.1"
CHUNK_BYTES = 65536
# The tail of every RESP command that subscribes: SUBSCRIBE, PSUBSCRIBE and SSUBSCRIBE.
SUBSCRIBING_COMMAND_TAIL = b"SUBSCRIBE\r\n"


@dataclass
class _Forward:
    """One client connection through the gate and the Redis connection it is forwarded to."""

    client: socket.socket
    upstream: socket.socket
    subscribing: bool = False

    def ends(self) -> tuple[socket.socket, socket.socket]:
        return self.client, self.upstream

    def note_sent(self, chunk: bytes) -> None:
        if SUBSCRIBING_COMMAND_TAIL in chunk.upper():
            self.subscribing = True


def _drop(forwards: list[_Forward]) -> None:
    for forward in forwards:
        for end in forward.ends():
            with contextlib.suppress(OSError):
                end.shutdown(socket.SHUT_RDWR)
            end.close()


def _pump(source: socket.socket, target: socket.socket, sent: Callable[[bytes], None] = lambda _: None) -> None:
    with contextlib.suppress(OSError):
        while chunk := source.recv(CHUNK_BYTES):
            sent(chunk)
            target.sendall(chunk)
    for end in (source, target):
        with contextlib.suppress(OSError):
            end.shutdown(socket.SHUT_RDWR)


class RedisGate:
    def __init__(self, redis_url: str, port: int = 0) -> None:
        target = urlsplit(redis_url)
        self._target = (target.hostname or LOCALHOST, target.port or 6379)
        self._listener = socket.socket()
        self._listener.bind((LOCALHOST, port))
        self._listener.listen()
        self._forwards: list[_Forward] = []
        self._lock = threading.Lock()
        self.url = f"redis://{LOCALHOST}:{self._listener.getsockname()[1]}{target.path}"
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            forward = _Forward(client, socket.create_connection(self._target))
            with self._lock:
                self._forwards.append(forward)
            threading.Thread(target=_pump, args=(client, forward.upstream, forward.note_sent), daemon=True).start()
            threading.Thread(target=_pump, args=(forward.upstream, client), daemon=True).start()

    def refuse_requests(self) -> None:
        """Redis answers no request of this gate's clients any more while their subscriptions stay up: no connection
        opens through the gate, and the gate drops every open one that never subscribed."""
        self._stop_listening()
        with self._lock:
            refused = [forward for forward in self._forwards if not forward.subscribing]
            self._forwards = [forward for forward in self._forwards if forward.subscribing]
        _drop(refused)

    def _stop_listening(self) -> None:
        # A listener that another thread is blocked in accept() on keeps listening after close() until it is shut down.
        with contextlib.suppress(OSError):
            self._listener.shutdown(socket.SHUT_RDWR)
        self._listener.close()

    def close(self) -> None:
        self._stop_listening()
        with self._lock:
            forwards, self._forwards = self._forwards, []
        _drop(forwards)
