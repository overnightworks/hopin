import json
import re
from typing import TYPE_CHECKING, Any

from agent_runtime_client.mcp_wire import LATEST_PROTOCOL_VERSION, PROTOCOL_VERSION_HEADER
from playwright.sync_api import Page, Playwright, expect

from hopin.agent.mcp import MCP_CLIENT_ID, MCP_GROUP, MCP_SCOPE
from hopin.agent.tools import JOIN_CALL
from hopin.config.constants import LOGIN_GROUP
from tests.e2e.support.people import PHONE

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.server import RunningServer

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


LABELS_ABOVE_THE_CONTROLS = """
() => {
    const stage = document.getElementById("stage");
    const reserved = parseFloat(getComputedStyle(stage).getPropertyValue("--stage-bottom"));
    const limit = stage.getBoundingClientRect().bottom - reserved;
    return [...stage.querySelectorAll(".tile .name")]
        .filter((label) => label.offsetParent !== null)
        .map((label) => label.getBoundingClientRect().bottom - limit);
}
"""
FULL_BLEED_TILES = 2


class Agent:
    """A person's own agent, calling HopIn's MCP endpoint with a token its client would hold."""

    def __init__(self, playwright: Playwright, server: RunningServer, person: Page, member_of: Callable[..., Any]):
        self._request = playwright.request.new_context(ignore_https_errors=True)
        self._server = server
        self._account = member_of(person).account
        self._room = re.search(r"/c/(\w+)", person.url).group(1)

    def dial_in(self, name: str) -> None:
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

    def close(self) -> None:
        self._request.dispose()


def assert_tiles_clear_of_the_controls(person: Page, tiles: int, form_factor: dict[str, Any]) -> None:
    """A phone shows one or two tiles full-bleed under the controls, so there only the names must clear them."""
    expect(person.locator("#stage")).to_have_attribute("data-count", str(tiles))
    full_bleed = form_factor is PHONE and tiles <= FULL_BLEED_TILES
    overhang = person.evaluate(LABELS_ABOVE_THE_CONTROLS if full_bleed else TILES_ABOVE_THE_CONTROLS)
    assert len(overhang) == (1 if full_bleed else tiles)
    assert all(extra <= TOLERANCE_PIXELS for extra in overhang), overhang
