"""One quiz turn in a real browser, on the client's real `GatewayBackend` over a scripted gateway."""

import secrets
from typing import TYPE_CHECKING

import pytest
from agent_runtime_client.backend.fake_gateway import FakeGateway, completion
from agent_runtime_client.backend.gateway import GatewayBackend
from playwright.sync_api import Page, expect

from hopin.agent.feature import GameFeature
from tests.e2e.game.game_page import GAME_MASTER_LINES, open_chat, start_quiz
from tests.e2e.support.calls import open_call
from tests.e2e.support.people import DESKTOP
from tests.e2e.support.server import RunningServer, loopback_sign_in, running_hopin
from tests.game.support.world import API_GROUP, agent_settings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from tests.support.signed_in import SeededMember

pytestmark = pytest.mark.e2e

QUESTION = "Welcher Fluss fließt durch Lissabon?"


@pytest.fixture(scope="module")
def gateway() -> FakeGateway:
    return FakeGateway(completion(QUESTION))


@pytest.fixture(scope="module")
def gateway_server(gateway: FakeGateway, tmp_path_factory: pytest.TempPathFactory) -> Iterator[RunningServer]:
    """HopIn with the game wired into its calls on the real backend, its gateway scripted."""
    directory = tmp_path_factory.mktemp("gateway-sign-in")
    key_file = directory / "gateway-key"
    key_file.write_text(f"scenario-key-{secrets.token_hex(16)}\n")
    game = GameFeature(GatewayBackend(agent_settings(key_file).agent, transport=gateway))
    with running_hopin(loopback_sign_in(directory), tmp_path_factory.mktemp("gateway-tls"), game) as server:
        yield server


@pytest.fixture
def e2e_server(gateway_server: RunningServer) -> RunningServer:
    return gateway_server


def test_a_member_of_the_api_group_starts_a_quiz_and_both_browsers_read_the_gateways_question(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    gateway: FakeGateway,
    member_of: Callable[[Page], SeededMember],
    page_errors: list[str],
) -> None:
    host, guest, _ = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)
    e2e_server.issuer.grant_groups(member_of(host).account.id, (API_GROUP,))

    start_quiz(host)

    for person in (host, guest):
        open_chat(person)
        expect(person.locator(GAME_MASTER_LINES)).to_contain_text(QUESTION)
    assert len(gateway.requests) == 1
