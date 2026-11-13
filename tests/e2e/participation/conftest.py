import secrets
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest

from tests.e2e.support.dialed_agent import (
    ADDERS_GROUP,
    AGENT_NAME,
    OWNER_SUBJECT,
    SECOND_AGENT_NAME,
    OwnAccountAgent,
    OwnAgent,
)
from tests.e2e.support.participation_scene import Scene
from tests.e2e.support.server import UNREACHABLE_HEARING, RunningServer, loopback_sign_in, running_hopin
from tests.support.fake_speech import FakeSpeech

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


@pytest.fixture
def scene_of(
    open_person: Callable[..., Page],
    member_of: Callable[[Page], Any],
    e2e_server: RunningServer,
    page_errors: list[str],
    grant_adders: Callable[[str], None],
    own_account_agent: OwnAccountAgent,
) -> Callable[[dict[str, Any]], Scene]:
    def build(form_factor: dict[str, Any]) -> Scene:
        return Scene(open_person, member_of, e2e_server, page_errors, form_factor, grant_adders, own_account_agent)

    return build
