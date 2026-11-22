import datetime
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from ipaddress import IPv4Address
from typing import TYPE_CHECKING

import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from speech_client.wire import VoiceId
from webauth.config import installed_web_auth_config

from hopin.agent.mcp import ParticipationFeature
from hopin.agent.speech_settings import SpeechService
from hopin.app import create_app
from hopin.main import uvicorn_options
from hopin.participation.figure_transport import get_figure_over_https
from hopin.participation.voice import VoiceStore
from hopin.signaling.handler import SignalingHandler
from hopin.signaling.scheduler import AsyncioScheduler
from hopin.web.page_build import BUILD_DIRECTORY, PageBuild
from tests.support.fake_issuer import FakeIssuer
from tests.support.in_thread_stores import in_thread_stores
from tests.support.sign_in_settings import TEST_REALM, build_sign_in_settings
from tests.support.stores import InThreadIdentity, single_process_stores

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator, Sequence
    from pathlib import Path

    from fastapi import FastAPI
    from webauth.config import WebAuthConfig

    from hopin.app import CallFeatureBuilder
    from hopin.config.settings import ServerSettings
    from hopin.participation.figures import FigureSource, FigureTransport
    from hopin.signaling.scheduler import TimerHandle
    from hopin.stores.shared import SharedStores
    from tests.support.sign_in_settings import SignInSettings

LOOPBACK_HOST = "127.0.0.1"

# A speech service no browser test reaches: the switch shows, and no speech call leaves the server.
UNREACHABLE_HEARING = SpeechService("http://speech.invalid:8090", VoiceId.THORSTEN, "speech-audience")

READINESS_POLL_INTERVAL_SECONDS = 0.1
READINESS_DEADLINE_SECONDS = 20
SERVER_STOP_TIMEOUT_SECONDS = 10
# A browser test cannot wait a whole recheck interval for the tick that ends an ended session.
RECHECK_INTERVAL_IN_TESTS_SECONDS = 1


class QuickRecheckScheduler(AsyncioScheduler):
    """The server's scheduler with the signaling handler's session recheck due after one second; every other timer
    keeps its delay."""

    def call_later(self, delay_seconds: float, callback: Callable[[], Awaitable[None]]) -> TimerHandle:
        # The recheck is scheduled as the handler's `_recheck_on_tick` bound to its connection.
        recheck = SignalingHandler._recheck_on_tick  # noqa: SLF001
        if isinstance(callback, partial) and getattr(callback.func, "__func__", None) is recheck:
            delay_seconds = RECHECK_INTERVAL_IN_TESTS_SECONDS
        return super().call_later(delay_seconds, callback)


def bound_loopback_socket() -> socket.socket:
    """A socket bound to a free loopback port, for `serving` to hand to its server: the port stays taken from its
    choice to the server's stop, so no other process can bind it in between."""
    listener = socket.socket()
    listener.bind((LOOPBACK_HOST, 0))
    return listener


def port_of(listener: socket.socket) -> int:
    return int(listener.getsockname()[1])


def _alternative_name(host: str) -> x509.GeneralName:
    try:
        return x509.IPAddress(IPv4Address(host))
    except ValueError:
        return x509.DNSName(host)


def write_self_signed_certificate(directory: Path, hosts: Sequence[str] = (LOOPBACK_HOST,)) -> tuple[Path, Path]:
    """A certificate for the loopback address or the named hosts, so the page is served over https like HopIn's public
    origin."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName(list(map(_alternative_name, hosts))), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_file, certificate_file = directory / "key.pem", directory / "certificate.pem"
    key_file.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    certificate_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return key_file, certificate_file


@contextmanager
def serving(server: uvicorn.Server, listener: socket.socket) -> Iterator[None]:
    """Run `server` on `listener` until the block ends; the listener is closed afterwards."""
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
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
        listener.close()
        assert not thread.is_alive(), f"HopIn did not stop within {SERVER_STOP_TIMEOUT_SECONDS} seconds"


@dataclass(frozen=True)
class RunningServer:
    base_url: str
    config: WebAuthConfig
    stores: InThreadIdentity
    issuer: FakeIssuer


@dataclass(frozen=True)
class LoopbackSignIn:
    """The sign-in of a HopIn on a free loopback port, held by `listener`, its issuer a fake."""

    listener: socket.socket
    host: str
    sign_in: SignInSettings
    stores: InThreadIdentity
    issuer: FakeIssuer
    shared_stores: SharedStores

    @property
    def port(self) -> int:
        return port_of(self.listener)


def loopback_sign_in(directory: Path, public_host: str = LOOPBACK_HOST) -> LoopbackSignIn:
    """The sign-in of a HopIn that listens on a free loopback port and is reached as `public_host`, which must
    resolve to the loopback address."""
    listener = bound_loopback_socket()
    port = port_of(listener)
    sign_in = build_sign_in_settings(
        directory,
        host=LOOPBACK_HOST,
        port=port,
        public_origin=f"https://{public_host}:{port}",
        trusted_proxies=LOOPBACK_HOST,
    )
    return LoopbackSignIn(
        listener, public_host, sign_in, single_process_stores(), FakeIssuer(sign_in), in_thread_stores()
    )


def participation_feature(
    loopback: LoopbackSignIn,
    figure_sources: tuple[FigureSource, ...],
    figure_transport: FigureTransport,
    speech: SpeechService,
    hears: bool,
    speaks: bool,
    voices: VoiceStore | None,
) -> ParticipationFeature:
    """The participation feature of a HopIn on `loopback`, hearing through `speech` only when `hears` and speaking
    through it only when `speaks`."""
    return ParticipationFeature(
        loopback.sign_in.settings.public_origin,
        figure_sources,
        figure_transport,
        hearing=speech if hears else None,
        speaking=speech if speaks else None,
        voices=voices or VoiceStore(),
        redis=loopback.shared_stores.async_redis,
        realm=TEST_REALM,
    )


def tls_server(app: FastAPI, settings: ServerSettings, certificate: tuple[Path, Path]) -> uvicorn.Server:
    key_file, certificate_file = certificate
    return uvicorn.Server(
        uvicorn.Config(app, **uvicorn_options(settings), ssl_keyfile=str(key_file), ssl_certfile=str(certificate_file))
    )


@contextmanager
def running_hopin(
    loopback: LoopbackSignIn,
    tls_directory: Path,
    call_feature: CallFeatureBuilder | None = None,
    page_build_directory: Path = BUILD_DIRECTORY,
    *,
    participation: bool = False,
    hears: bool = False,
    speaks: bool = False,
    speech: SpeechService = UNREACHABLE_HEARING,
    certificate: tuple[Path, Path] | None = None,
    more_call_features: Sequence[CallFeatureBuilder] = (),
    figure_sources: tuple[FigureSource, ...] = (),
    figure_transport: FigureTransport = get_figure_over_https,
    voices: VoiceStore | None = None,
) -> Iterator[RunningServer]:
    """Serve HopIn over https on `loopback`, with the call's feature and participation only when asked for, and with
    the setting that lets agents hear on when `hears` and the one that lets them speak when `speaks`, through `speech`;
    `figure_sources` are where participation may fetch an agent's figure from, through `figure_transport`, and
    `more_call_features` wires further features beside `call_feature`, and `voices` is the store the game's voices
    and participation's route share.

    The page comes from `page_build_directory`, so a test can serve a copy it changes, and over `certificate` (key
    file, certificate file) when one is given.
    """
    app = create_app(
        loopback.sign_in.settings,
        TEST_REALM,
        page_build=PageBuild.load(page_build_directory),
        shared_stores=loopback.shared_stores,
        scheduler=QuickRecheckScheduler(),
        identity=loopback.stores,
        issuer_transports=loopback.issuer.transports,
        call_features=(
            *(() if call_feature is None else (call_feature,)),
            *more_call_features,
            *(
                (participation_feature(loopback, figure_sources, figure_transport, speech, hears, speaks, voices),)
                if participation
                else ()
            ),
        ),
    )
    with serving(
        tls_server(app, loopback.sign_in.settings, certificate or write_self_signed_certificate(tls_directory)),
        loopback.listener,
    ):
        yield RunningServer(
            f"https://{loopback.host}:{loopback.port}", installed_web_auth_config(app), loopback.stores, loopback.issuer
        )
