import datetime
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from ipaddress import IPv4Address
from typing import TYPE_CHECKING

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import uvicorn
    from webauth.config import WebAuthConfig

    from hopin.auth.stores import SignInStores

LOOPBACK_HOST = "127.0.0.1"

READINESS_POLL_INTERVAL_SECONDS = 0.1
READINESS_DEADLINE_SECONDS = 20
SERVER_STOP_TIMEOUT_SECONDS = 10


def _reserve_free_port() -> int:
    with socket.socket() as probe:
        probe.bind((LOOPBACK_HOST, 0))
        return int(probe.getsockname()[1])


def _write_self_signed_certificate(directory: Path) -> tuple[Path, Path]:
    """A certificate for the loopback address, so the page is served over https like HopIn's public origin."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, LOOPBACK_HOST)])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(IPv4Address(LOOPBACK_HOST))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_file, certificate_file = directory / "key.pem", directory / "certificate.pem"
    key_file.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    certificate_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return key_file, certificate_file


@contextmanager
def _serving(server: uvicorn.Server) -> Iterator[None]:
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + READINESS_DEADLINE_SECONDS
    try:
        while not server.started:
            if not thread.is_alive():
                pytest.fail("HopIn exited before it started")
            if time.monotonic() > deadline:
                pytest.fail(f"HopIn did not start within {READINESS_DEADLINE_SECONDS} seconds")
            time.sleep(READINESS_POLL_INTERVAL_SECONDS)
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=SERVER_STOP_TIMEOUT_SECONDS)
        assert not thread.is_alive(), f"HopIn did not stop within {SERVER_STOP_TIMEOUT_SECONDS} seconds"


@dataclass(frozen=True)
class RunningServer:
    base_url: str
    config: WebAuthConfig
    stores: SignInStores
