from typing import TYPE_CHECKING

import pytest

from tests.e2e.support.dialed_agent import OwnAccountAgent
from tests.e2e.support.server import RunningServer, loopback_sign_in, running_hopin

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from playwright.sync_api import Playwright

OWNER_USERNAME = "owner"
OWNER_SUBJECT = f"subject-{OWNER_USERNAME}"
ADDERS_GROUP = "/apps/hopin/agent-adders"
AGENT_NAME = "Mozart"


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
def own_account_agent(playwright: Playwright, e2e_server: RunningServer) -> Iterator[OwnAccountAgent]:
    """The scripted agent of `OWNER_SUBJECT`."""
    agent = OwnAccountAgent(playwright, e2e_server, OWNER_SUBJECT, AGENT_NAME)
    yield agent
    agent.close()


@pytest.fixture
def grant_adders(e2e_server: RunningServer) -> Callable[[str], None]:
    """Make the issuer hold the adders group for the subject."""

    def grant(subject: str) -> None:
        e2e_server.issuer.grant_groups(subject, (ADDERS_GROUP,))

    return grant
