from typing import TYPE_CHECKING

import pytest

from hopin.agent.feature import GameFeature
from tests.e2e.support.server import RunningServer, loopback_sign_in, running_hopin
from tests.game.support.fake_agent_backend import FakeAgentBackend

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(scope="session")
def agent_backend() -> FakeAgentBackend:
    return FakeAgentBackend()


@pytest.fixture(autouse=True)
def _drop_queued_agent_answers(agent_backend: FakeAgentBackend) -> Iterator[None]:
    """The session's one fake outlives every test, so a failing test must not leave its answers for the next."""
    yield
    agent_backend.reset()


@pytest.fixture(scope="session")
def participation_server(
    agent_backend: FakeAgentBackend, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[RunningServer]:
    """HopIn with the game and participation wired into its calls, the game's agent backend a fake."""
    loopback = loopback_sign_in(tmp_path_factory.mktemp("participation-sign-in"))
    with running_hopin(
        loopback, tmp_path_factory.mktemp("participation-tls"), GameFeature(agent_backend), participation=True
    ) as server:
        yield server


@pytest.fixture
def e2e_server(participation_server: RunningServer) -> RunningServer:
    return participation_server
