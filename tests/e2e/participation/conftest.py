import secrets
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest
from speech_client.wire import VoiceId

from hopin.agent.speech_settings import SpeechService
from hopin.participation.settings import ParticipationSettings
from tests.e2e.support.dialed_agent import (
    AGENT_NAME,
    OWNER_SUBJECT,
    SECOND_AGENT_NAME,
    OwnAccountAgent,
    OwnAgent,
)
from tests.e2e.support.mesh_pair import ConnectedPair, pair_connector
from tests.e2e.support.participation_scene import Scene
from tests.e2e.support.server import UNREACHABLE_HEARING, RunningServer, loopback_sign_in, running_hopin
from tests.e2e.support.speaker import Speaker, speaking_hopin
from tests.participation.support.figures import SOURCE_PREFIX, FigureOrigin
from tests.support.agent_adders import ADDERS_GROUP
from tests.support.fake_speech import FakeSpeech
from tests.support.loopback_origin import Origin
from tests.support.speech_origin import AUDIENCE_SCOPE, offering

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from playwright.sync_api import Page


@pytest.fixture(scope="session")
def participation_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[RunningServer]:
    """HopIn with participation wired into its calls, the adders group mapped to the owner `OWNER_SUBJECT`."""
    loopback = loopback_sign_in(tmp_path_factory.mktemp("participation-sign-in"))
    with running_hopin(
        loopback,
        tmp_path_factory.mktemp("participation-tls"),
        participation=True,
        agent_adders={OWNER_SUBJECT: ADDERS_GROUP},
    ) as server:
        yield server


@pytest.fixture(scope="session")
def hearing_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[RunningServer]:
    """`participation_server` with the setting that lets agents hear on."""
    loopback = loopback_sign_in(tmp_path_factory.mktemp("hearing-sign-in"))
    with running_hopin(
        loopback,
        tmp_path_factory.mktemp("hearing-tls"),
        participation=True,
        hears=True,
        agent_adders={OWNER_SUBJECT: ADDERS_GROUP},
    ) as server:
        yield server


@pytest.fixture(scope="session")
def fake_speech() -> Iterator[FakeSpeech]:
    """A speech service on a loopback port, which records each hop's session and says what a test scripts."""
    service = FakeSpeech()
    service.start()
    yield service
    service.stop()


@pytest.fixture(scope="session")
def speaking_server(tmp_path_factory: pytest.TempPathFactory, fake_speech: FakeSpeech) -> Iterator[RunningServer]:
    """`hearing_server` whose speech service is `fake_speech`."""
    loopback = loopback_sign_in(tmp_path_factory.mktemp("speaking-sign-in"))
    with running_hopin(
        loopback,
        tmp_path_factory.mktemp("speaking-tls"),
        participation=True,
        hears=True,
        speech=replace(UNREACHABLE_HEARING, origin=fake_speech.origin),
        agent_adders={OWNER_SUBJECT: ADDERS_GROUP},
    ) as server:
        yield server


@pytest.fixture
def e2e_server(participation_server: RunningServer) -> RunningServer:
    return participation_server


@pytest.fixture
def own_account_agent(e2e_server: RunningServer) -> Iterator[OwnAccountAgent]:
    """The scripted agent of `OWNER_SUBJECT`, named apart from the agents earlier tests left registered."""
    agent = OwnAccountAgent(e2e_server, OWNER_SUBJECT, f"{AGENT_NAME}-{secrets.token_hex(3)}")
    yield agent
    agent.close()


@pytest.fixture
def second_own_account_agent(e2e_server: RunningServer) -> Iterator[OwnAccountAgent]:
    """Another scripted agent of `OWNER_SUBJECT`, so the owner has two to bring into one call."""
    agent = OwnAccountAgent(e2e_server, OWNER_SUBJECT, f"{SECOND_AGENT_NAME}-{secrets.token_hex(3)}")
    yield agent
    agent.close()


@pytest.fixture
def grant_adders(e2e_server: RunningServer) -> Callable[[str], None]:
    """Make the issuer hold the adders group for the subject."""

    def grant(subject: str) -> None:
        e2e_server.issuer.grant_groups(subject, (ADDERS_GROUP,))

    return grant


@pytest.fixture
def own_agent_of(e2e_server: RunningServer) -> Iterator[Callable[[str], OwnAgent]]:
    """A person's own scripted agent by the person's account id; each ends with the test."""
    agents: list[OwnAgent] = []

    def make(account_id: str) -> OwnAgent:
        agents.append(OwnAgent(e2e_server, account_id))
        return agents[-1]

    yield make
    for agent in agents:
        agent.close()


@pytest.fixture(scope="module")
def speech_origin() -> Iterator[Origin]:
    """A speech origin that offers every voice."""
    origin = Origin()
    offering(origin, *VoiceId)
    yield origin
    origin.close()


@pytest.fixture(scope="module")
def voicing_server(tmp_path_factory: pytest.TempPathFactory, speech_origin: Origin) -> Iterator[RunningServer]:
    """HopIn whose agents speak through `speech_origin`."""
    with speaking_hopin(tmp_path_factory, "voice", speech_origin.base_url) as server:
        yield server


@pytest.fixture(scope="module")
def figure_server(
    tmp_path_factory: pytest.TempPathFactory, figure_origin: FigureOrigin, speech_origin: Origin
) -> Iterator[RunningServer]:
    """HopIn with participation that allows figures from the test source, fetched through `figure_origin`, and lets
    agents speak through `speech_origin`."""
    loopback = loopback_sign_in(tmp_path_factory.mktemp("figure-sign-in"))
    with running_hopin(
        loopback,
        tmp_path_factory.mktemp("figure-tls"),
        participation=True,
        speaks=True,
        speech=SpeechService(speech_origin.base_url, VoiceId.THORSTEN, AUDIENCE_SCOPE),
        agent_adders={OWNER_SUBJECT: ADDERS_GROUP},
        figure_sources=ParticipationSettings(figure_sources=[SOURCE_PREFIX]).figure_sources,
        figure_transport=figure_origin,
    ) as server:
        yield server


@pytest.fixture
def speaker(scene_of: Callable[[dict[str, Any]], Scene], speech_origin: Origin, form_factor: dict[str, Any]) -> Speaker:
    return Speaker(scene_of(form_factor), speech_origin)


@pytest.fixture
def scene_of(
    open_person: Callable[..., Page],
    member_of: Callable[[Page], Any],
    e2e_server: RunningServer,
    page_errors: list[str],
    grant_adders: Callable[[str], None],
    own_account_agent: OwnAccountAgent,
) -> Callable[..., Scene]:
    def build(form_factor: dict[str, Any], *, owner_adds: bool = False) -> Scene:
        return Scene(
            open_person,
            member_of,
            e2e_server,
            page_errors,
            form_factor,
            grant_adders,
            own_account_agent,
            owner_adds=owner_adds,
        )

    return build


@pytest.fixture
def connected_pair(open_person: Callable[..., Page], e2e_server: RunningServer) -> Iterator[ConnectedPair]:
    """Two people in a call on this directory's server, each behind a tap on its signaling socket."""
    for connect in pair_connector(open_person, e2e_server.base_url):
        yield connect()
