import datetime
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from ipaddress import IPv4Address
from typing import TYPE_CHECKING

import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from webauth.config import installed_web_auth_config

from hopin.app import create_app
from hopin.auth.stores import SignInStores
from hopin.main import uvicorn_options
from tests.support.fake_issuer import FakeIssuer
from tests.support.sign_in_settings import build_sign_in_settings

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from webauth.config import WebAuthConfig

    from hopin.app import CallFeatureBuilder
    from tests.support.sign_in_settings import SignInSettings

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
    issuer: FakeIssuer


@dataclass(frozen=True)
class LoopbackSignIn:
    """The sign-in of a HopIn on a free loopback port, its issuer a fake."""

    port: int
    sign_in: SignInSettings
    stores: SignInStores
    issuer: FakeIssuer


def loopback_sign_in(directory: Path) -> LoopbackSignIn:
    port = _reserve_free_port()
    sign_in = build_sign_in_settings(
        directory,
        host=LOOPBACK_HOST,
        port=port,
        public_origin=f"https://{LOOPBACK_HOST}:{port}",
        trusted_proxies=LOOPBACK_HOST,
    )
    return LoopbackSignIn(port, sign_in, SignInStores.in_memory(), FakeIssuer(sign_in))


@contextmanager
def running_hopin(
    loopback: LoopbackSignIn, tls_directory: Path, call_feature: CallFeatureBuilder | None = None
) -> Iterator[RunningServer]:
    """Serve HopIn over https on `loopback`, with the call's feature only when one is given."""
    app = create_app(
        loopback.sign_in.settings,
        stores=loopback.stores,
        issuer_transports=loopback.issuer.transports,
        call_feature=call_feature,
    )
    key_file, certificate_file = _write_self_signed_certificate(tls_directory)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            **uvicorn_options(loopback.sign_in.settings),
            ssl_keyfile=str(key_file),
            ssl_certfile=str(certificate_file),
        )
    )
    with _serving(server):
        yield RunningServer(
            f"https://{LOOPBACK_HOST}:{loopback.port}", installed_web_auth_config(app), loopback.stores, loopback.issuer
        )
