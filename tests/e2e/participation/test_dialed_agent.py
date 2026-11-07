import json
import re
from typing import TYPE_CHECKING, Any

import pytest
from agent_runtime_client.mcp_wire import LATEST_PROTOCOL_VERSION, PROTOCOL_VERSION_HEADER
from playwright.sync_api import Page, Playwright, expect

from hopin.agent.mcp import MCP_CLIENT_ID, MCP_GROUP, MCP_SCOPE
from hopin.agent.tools import JOIN_CALL
from hopin.config.constants import LOGIN_GROUP
from tests.e2e.support.calls import join, open_call
from tests.e2e.support.media import RECORD_PEER_CONNECTIONS, wait_for_peer_count
from tests.e2e.support.people import DESKTOP, FORM_FACTORS

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

AGENT_TILE = '#stage [id^="container-agent-"]'
REMOVE = ".agent-remove"
COUNT = "#participantCount .num"
TOLERANCE_PIXELS = 1
TILES_ABOVE_THE_CONTROLS = """
() => {
    const stage = document.getElementById("stage");
    const reserved = parseFloat(getComputedStyle(stage).getPropertyValue("--stage-bottom"));
    const limit = stage.getBoundingClientRect().bottom - reserved;
    return [...stage.querySelectorAll(".tile")].map((tile) => tile.getBoundingClientRect().bottom - limit);
}
"""


class Agent:
    """A person's own agent, calling HopIn's MCP endpoint with a token its client would hold."""

    def __init__(self, playwright: Playwright, server: RunningServer, person: Page, member_of: Callable[..., Any]):
        self._request = playwright.request.new_context(ignore_https_errors=True)
        self._server = server
        self._account = member_of(person).account
        self._room = re.search(r"/c/(\w+)", person.url).group(1)

    def dial_in(self, name: str) -> str:
        token = self._server.issuer.access_token(
            self._account.id,
            audience=f"{self._server.base_url}/mcp",
            authorized_party=MCP_CLIENT_ID,
            scope=MCP_SCOPE,
            groups=(LOGIN_GROUP, MCP_GROUP),
        )
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": JOIN_CALL, "arguments": {"room": self._room, "name": name}},
        }
        response = self._request.post(
            f"{self._server.base_url}/mcp",
            data=json.dumps(body),
            headers={
                "authorization": f"Bearer {token}",
                "content-type": "application/json",
                PROTOCOL_VERSION_HEADER: LATEST_PROTOCOL_VERSION,
            },
        )
        result = response.json()["result"]
        assert not result["isError"], result
        return json.loads(result["content"][0]["text"])["agent_id"]

    def close(self) -> None:
        self._request.dispose()


@pytest.fixture
def agent_of(
    playwright: Playwright, e2e_server: RunningServer, member_of: Callable[..., Any]
) -> Callable[[Page], Agent]:
    agents: list[Agent] = []

    def make(person: Page) -> Agent:
        agents.append(Agent(playwright, e2e_server, person, member_of))
        return agents[-1]

    yield make
    for agent in agents:
        agent.close()


def test_a_dialed_in_agent_shows_as_a_tile_every_page_sees_and_its_sponsor_or_the_host_removes_it(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_of: Callable[[Page], Agent],
) -> None:
    """CALL-48, CALL-49, CALL-50: A dialed-in agent shows with its sponsor; its sponsor or the host removes it."""
    host, guest, invite_link = open_call(open_person, e2e_server.base_url, page_errors, DESKTOP)
    bystander = join(open_person, invite_link, DESKTOP, (RECORD_PEER_CONNECTIONS,), page_errors)
    wait_for_peer_count(bystander, 2)

    agent_of(bystander).dial_in("Claude")

    for person in (host, guest, bystander):
        tile = person.locator(AGENT_TILE)
        expect(tile).to_have_count(1)
        expect(tile.locator(".name")).to_contain_text("Claude")
        expect(tile.locator(".ai-badge")).to_be_visible()
        expect(tile.locator(".sponsor")).to_be_visible()
    expect(host.locator(COUNT)).to_have_text("3")
    expect(host.locator(AGENT_TILE + " " + REMOVE)).to_have_count(1)
    expect(bystander.locator(AGENT_TILE + " " + REMOVE)).to_have_count(1)
    expect(guest.locator(AGENT_TILE + " " + REMOVE)).to_have_count(0)

    bystander.locator(AGENT_TILE + " " + REMOVE).click()

    for person in (host, guest, bystander):
        expect(person.locator(AGENT_TILE)).to_have_count(0)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_six_people_and_two_dialed_in_agents_are_eight_tiles_the_count_names_people_and_the_controls_stay_clear(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    agent_of: Callable[[Page], Agent],
    form_factor: dict[str, Any],
) -> None:
    """CALL-47: Six people and two agents are eight tiles, the count names people only, the controls stay clear."""
    host, guest, invite_link = open_call(open_person, e2e_server.base_url, page_errors, form_factor)
    for _ in range(4):
        join(open_person, invite_link, form_factor, (), page_errors)

    agent_of(host).dial_in("Claude")
    agent_of(guest).dial_in("Codex")

    expect(host.locator("#stage")).to_have_attribute("data-count", "8")
    expect(host.locator(COUNT)).to_have_text("6")
    overhang = host.evaluate(TILES_ABOVE_THE_CONTROLS)
    assert len(overhang) == 8
    assert all(extra <= TOLERANCE_PIXELS for extra in overhang)
