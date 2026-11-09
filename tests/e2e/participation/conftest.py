import secrets
from typing import TYPE_CHECKING, Any

import pytest

from tests.e2e.support.dialed_agent import (
    ADDERS_GROUP,
    AGENT_NAME,
    OWNER_SUBJECT,
    OwnAccountAgent,
    OwnAgent,
)
from tests.e2e.support.participation_scene import Scene
from tests.e2e.support.server import RunningServer, loopback_sign_in, running_hopin

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
