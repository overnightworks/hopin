"""A TCP forwarder in front of the Redis a test runs against; closing it is that Redis going down for its clients."""

import contextlib
import socket
import threading
from urllib.parse import urlsplit

LOCALHOST = "127.0.0.1"
CHUNK_BYTES = 65536


def _pump(source: socket.socket, target: socket.socket) -> None:
    with contextlib.suppress(OSError):
        while chunk := source.recv(CHUNK_BYTES):
            target.sendall(chunk)
    for end in (source, target):
        with contextlib.suppress(OSError):
            end.shutdown(socket.SHUT_RDWR)


class RedisGate:
    def __init__(self, redis_url: str) -> None:
        target = urlsplit(redis_url)
        self._target = (target.hostname or LOCALHOST, target.port or 6379)
        self._listener = socket.socket()
        self._listener.bind((LOCALHOST, 0))
        self._listener.listen()
        self._connections: list[socket.socket] = []
        self._lock = threading.Lock()
        self.url = f"redis://{LOCALHOST}:{self._listener.getsockname()[1]}{target.path}"
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            upstream = socket.create_connection(self._target)
            with self._lock:
                self._connections += [client, upstream]
            threading.Thread(target=_pump, args=(client, upstream), daemon=True).start()
            threading.Thread(target=_pump, args=(upstream, client), daemon=True).start()

    def close(self) -> None:
        # A listener that another thread is blocked in accept() on keeps listening after close() until it is shut down.
        with contextlib.suppress(OSError):
            self._listener.shutdown(socket.SHUT_RDWR)
        self._listener.close()
        with self._lock:
            connections, self._connections = self._connections, []
        for connection in connections:
            with contextlib.suppress(OSError):
                connection.shutdown(socket.SHUT_RDWR)
            connection.close()
