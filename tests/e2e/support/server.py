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
from speech_client.wire import VoiceId
from webauth.config import installed_web_auth_config

from hopin.agent.mcp import ParticipationFeature
from hopin.agent.speech_settings import SpeechService
from hopin.app import create_app
from hopin.main import uvicorn_options
from hopin.participation.figure_transport import get_figure_over_https
from hopin.participation.voice import VoiceStore
from hopin.web.page_build import BUILD_DIRECTORY, PageBuild
from tests.support.fake_issuer import FakeIssuer
from tests.support.sign_in_settings import build_sign_in_settings
from tests.support.stores import single_process_stores

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from pathlib import Path

    from webauth.config import WebAuthConfig
    from webauth.single_process_stores import SingleProcessStores

    from hopin.app import CallFeatureBuilder
    from hopin.participation.figures import FigureSource, FigureTransport
    from tests.support.sign_in_settings import SignInSettings

LOOPBACK_HOST = "127.0.0.1"

# A speech service no browser test reaches: the switch shows, and no speech call leaves the server.
UNREACHABLE_HEARING = SpeechService("http://speech.invalid:8090", VoiceId.THORSTEN, "speech-audience")

READINESS_POLL_INTERVAL_SECONDS = 0.1
READINESS_DEADLINE_SECONDS = 20
SERVER_STOP_TIMEOUT_SECONDS = 10


def reserve_free_port() -> int:
    with socket.socket() as probe:
        probe.bind((LOOPBACK_HOST, 0))
        return int(probe.getsockname()[1])


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
def serving(server: uvicorn.Server) -> Iterator[None]:
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
    stores: SingleProcessStores
    issuer: FakeIssuer


@dataclass(frozen=True)
class LoopbackSignIn:
    """The sign-in of a HopIn on a free loopback port, its issuer a fake."""

    port: int
    host: str
    sign_in: SignInSettings
    stores: SingleProcessStores
    issuer: FakeIssuer


def loopback_sign_in(directory: Path, public_host: str = LOOPBACK_HOST) -> LoopbackSignIn:
    """The sign-in of a HopIn that listens on a free loopback port and is reached as `public_host`, which must
    resolve to the loopback address."""
    port = reserve_free_port()
    sign_in = build_sign_in_settings(
        directory,
        host=LOOPBACK_HOST,
        port=port,
        public_origin=f"https://{public_host}:{port}",
        trusted_proxies=LOOPBACK_HOST,
    )
    return LoopbackSignIn(port, public_host, sign_in, single_process_stores(), FakeIssuer(sign_in))


def participation_feature(
    loopback: LoopbackSignIn,
    agent_adders: Mapping[str, str] | None,
    figure_sources: tuple[FigureSource, ...],
    figure_transport: FigureTransport,
    hears: bool,
    voices: VoiceStore | None,
) -> ParticipationFeature:
    """Participation at the loopback's public origin, its hearing a speech service nobody reaches when `hears`."""
    return ParticipationFeature(
        loopback.sign_in.settings.public_origin,
        agent_adders or {},
        figure_sources,
        figure_transport,
        hearing=UNREACHABLE_HEARING if hears else None,
        voices=voices or VoiceStore(),
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
    agent_adders: Mapping[str, str] | None = None,
    certificate: tuple[Path, Path] | None = None,
    more_call_features: Sequence[CallFeatureBuilder] = (),
    figure_sources: tuple[FigureSource, ...] = (),
    figure_transport: FigureTransport = get_figure_over_https,
    voices: VoiceStore | None = None,
) -> Iterator[RunningServer]:
    """Serve HopIn over https on `loopback`, with the call's feature and participation only when asked for, and with
    the setting that lets agents hear on when `hears`;
    `agent_adders` maps an owner's subject to the group whose members may add that owner's agent,
    `figure_sources` are where participation may fetch an agent's figure from, through `figure_transport`, and
    `more_call_features` wires further features beside `call_feature`, and `voices` is the store the game's voices
    and participation's route share.

    The page comes from `page_build_directory`, so a test can serve a copy it changes, and over `certificate` (key
    file, certificate file) when one is given.
    """
    app = create_app(
        loopback.sign_in.settings,
        page_build=PageBuild.load(page_build_directory),
        stores=loopback.stores,
        issuer_transports=loopback.issuer.transports,
        call_features=(
            *(() if call_feature is None else (call_feature,)),
            *more_call_features,
            *(
                (participation_feature(loopback, agent_adders, figure_sources, figure_transport, hears, voices),)
                if participation
                else ()
            ),
        ),
    )
    key_file, certificate_file = certificate or write_self_signed_certificate(tls_directory)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            **uvicorn_options(loopback.sign_in.settings),
            ssl_keyfile=str(key_file),
            ssl_certfile=str(certificate_file),
        )
    )
    with serving(server):
        yield RunningServer(
            f"https://{loopback.host}:{loopback.port}", installed_web_auth_config(app), loopback.stores, loopback.issuer
        )
