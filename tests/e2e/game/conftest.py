from typing import TYPE_CHECKING

import pytest

from hopin.game import GameSettings, game_feature
from tests.e2e.support.server import LoopbackSignIn, RunningServer, loopback_sign_in, running_hopin
from tests.game.support.fake_agent_service import FakeAgentService

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(scope="session")
def game_sign_in(tmp_path_factory: pytest.TempPathFactory) -> LoopbackSignIn:
    return loopback_sign_in(tmp_path_factory.mktemp("game-sign-in"))


@pytest.fixture(scope="session")
def agent_service(game_sign_in: LoopbackSignIn) -> FakeAgentService:
    return FakeAgentService(game_sign_in.issuer)


@pytest.fixture(autouse=True)
def _drop_queued_agent_answers(agent_service: FakeAgentService) -> Iterator[None]:
    """The session's one fake outlives every test, so a failing test must not leave its answers for the next."""
    yield
    agent_service.reset()


@pytest.fixture(scope="session")
def game_server(
    game_sign_in: LoopbackSignIn, agent_service: FakeAgentService, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[RunningServer]:
    """HopIn with the game wired into its calls, its agent service a fake."""
    game = game_feature(GameSettings(), transport=agent_service.transport)
    with running_hopin(game_sign_in, tmp_path_factory.mktemp("game-tls"), game) as server:
        yield server


@pytest.fixture
def e2e_server(game_server: RunningServer) -> RunningServer:
    return game_server
