"""A loopback origin for transport tests, over HTTP or TLS: real sockets, answers from a script, held until released or
dripped."""

import datetime
import socket
import ssl
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

if TYPE_CHECKING:
    from pathlib import Path

LOCALHOST = "127.0.0.1"
LOCALHOST_NAME = "localhost"
ARRIVAL_CAP_SECONDS = 10
CERTIFICATE_DAYS = 1


def http_response(
    status_line: str, body: bytes, *, extra_header: bytes = b"", declared_length: int | None = None
) -> bytes:
    length = len(body) if declared_length is None else declared_length
    head = f"HTTP/1.1 {status_line}\r\nContent-Length: {length}\r\nConnection: close\r\n".encode()
    return head + extra_header + b"\r\n" + body


def tls_context_for(name: str, directory: Path) -> tuple[ssl.SSLContext, Path]:
    """A server context with a fresh self-signed certificate for `name`, and the path of that certificate in
    `directory`, the file a client trusts to accept it."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=CERTIFICATE_DAYS))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    certificate_path, key_path = directory / f"{name}.pem", directory / f"{name}.key"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate_path, key_path)
    return context, certificate_path


@dataclass
class Origin:
    """A loopback origin, over HTTP or, with a `tls` context, over HTTPS at `localhost`: it answers each path from a
    script and can hold an answer until released."""

    tls: ssl.SSLContext | None = None

    requests: list[tuple[str, str, dict[str, str], bytes]] = field(default_factory=list)
    received: dict[str, threading.Event] = field(default_factory=dict)
    release: dict[str, threading.Event] = field(default_factory=dict)
    scripts: dict[str, bytes] = field(default_factory=dict)
    drips: dict[str, tuple[bytes, float]] = field(default_factory=dict)
    stopped: threading.Event = field(default_factory=threading.Event)
    listener: socket.socket = field(default_factory=socket.socket)
    threads: list[threading.Thread] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.listener.bind((LOCALHOST, 0))
        self.listener.listen()
        self.port = self.listener.getsockname()[1]
        self.base_url = (
            f"http://{LOCALHOST}:{self.port}" if self.tls is None else f"https://{LOCALHOST_NAME}:{self.port}"
        )
        self._accepting = threading.Thread(target=self._accept)
        self._accepting.start()

    def serve(self, path: str, answer: bytes, *, held: bool = False, dripped: bytes = b"", every: float = 0.0) -> str:
        """Answer the path with `answer`, then with `dripped` one byte at a time, a byte `every` seconds."""
        self.scripts[path] = answer
        self.drips[path] = (dripped, every)
        self.received[path] = threading.Event()
        self.release[path] = threading.Event()
        if not held:
            self.release[path].set()
        return f"{self.base_url}{path}"

    def paths_requested(self) -> list[str]:
        return [path for _, path, _, _ in self.requests]

    def close(self) -> None:
        self.stopped.set()
        for release in self.release.values():
            release.set()
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

    def _answer(self, accepted: socket.socket) -> None:
        with accepted:
            try:
                connection = accepted if self.tls is None else self.tls.wrap_socket(accepted, server_side=True)
            except OSError:
                return
            with connection:
                self._respond(connection)

    def _respond(self, connection: socket.socket) -> None:
        try:
            request = _read_request(connection)
        except OSError:
            return
        if request is None:
            return
        self.requests.append(request)
        _, path, _, _ = request
        if path not in self.scripts:
            connection.sendall(http_response("404 Not Found", b""))
            return
        self.received[path].set()
        self.release[path].wait(ARRIVAL_CAP_SECONDS)
        dripped, every = self.drips[path]
        try:
            connection.sendall(self.scripts[path])
            for byte in dripped:
                if self.stopped.wait(every):
                    return
                connection.sendall(bytes([byte]))
        except OSError:
            return


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
