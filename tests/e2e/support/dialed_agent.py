import json
import re
import secrets
from typing import TYPE_CHECKING, Any

from agent_runtime_client.mcp_wire import LATEST_PROTOCOL_VERSION, PROTOCOL_VERSION_HEADER
from playwright.sync_api import Page, Playwright, expect

from hopin.agent.mcp import AGENT_GROUP, MCP_CLIENT_ID, MCP_GROUP, MCP_SCOPE
from hopin.agent.tools import ACCEPT_INVITATION, JOIN_CALL, SAY, WAIT
from hopin.config.constants import LOGIN_GROUP
from tests.e2e.support.people import PHONE

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.server import RunningServer

OWNER_USERNAME = "owner"
OWNER_SUBJECT = f"subject-{OWNER_USERNAME}"
ADDERS_GROUP = "/apps/hopin/agent-adders"
AGENT_NAME = "Mozart"
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
        self._call(JOIN_CALL, {"room": self._room, "name": name})

    def say(self, text: str) -> None:
        self._call(SAY, {"room": self._room, "text": text})

    def _call(self, tool: str, arguments: dict[str, Any]) -> None:
        token = self._server.issuer.access_token(
            self._account.id,
            audience=f"{self._server.base_url}/mcp",
            authorized_party=MCP_CLIENT_ID,
            scope=MCP_SCOPE,
            groups=(LOGIN_GROUP, MCP_GROUP),
        )
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
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


class OwnAccountAgent:
    """An agent with an account of its own, as a scripted client: it signs in with a client-credentials shaped token
    and calls HopIn's MCP endpoint the way the runner does."""

    def __init__(self, playwright: Playwright, server: RunningServer, owner_subject: str, name: str):
        self._request = playwright.request.new_context(ignore_https_errors=True)
        self._server = server
        self.name = name
        self._token = server.issuer.access_token(
            f"agent-of-{owner_subject}-{secrets.token_hex(4)}",
            audience=f"{server.base_url}/mcp",
            authorized_party="some-agent-client",
            scope=MCP_SCOPE,
            groups=(AGENT_GROUP, MCP_GROUP),
            extra_claims={"agent_owner": owner_subject, "agent_name": name},
        )

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
        response = self._request.post(
            f"{self._server.base_url}/mcp",
            data=json.dumps(body),
            headers={
                "authorization": f"Bearer {self._token}",
                "content-type": "application/json",
                PROTOCOL_VERSION_HEADER: LATEST_PROTOCOL_VERSION,
            },
        )
        return json.loads(response.json()["result"]["content"][0]["text"])

    def standings(self) -> list[dict[str, Any]]:
        return self.call(WAIT, {})["standings"]

    def invitation_within(self, seconds: int) -> dict[str, Any]:
        """The standing that holds an invitation, waiting up to `seconds` for it."""
        answer = self.call(WAIT, {})
        for _ in range(seconds):
            invited = [standing for standing in answer["standings"] if standing["state"] == "invited"]
            if invited:
                return invited[0]
            answer = self.call(WAIT, {"after": answer["cursor"], "timeout_seconds": 1})
        raise AssertionError("the agent was not invited")

    def accept(self, standing: dict[str, Any]) -> dict[str, Any]:
        invitation = standing["invitation"]
        return self.call(
            ACCEPT_INVITATION,
            {"room": standing["place"]["id"], "invitation": invitation["id"], "epoch": invitation["epoch"]},
        )

    def close(self) -> None:
        self._request.dispose()


def assert_tiles_clear_of_the_controls(person: Page, tiles: int, form_factor: dict[str, Any]) -> None:
    """A phone shows one or two tiles full-bleed under the controls, so there only the names must clear them."""
    expect(person.locator("#stage")).to_have_attribute("data-count", str(tiles))
    full_bleed = form_factor is PHONE and tiles <= FULL_BLEED_TILES
    overhang = person.evaluate(LABELS_ABOVE_THE_CONTROLS if full_bleed else TILES_ABOVE_THE_CONTROLS)
    assert len(overhang) == (1 if full_bleed else tiles)
    assert all(extra <= TOLERANCE_PIXELS for extra in overhang), overhang
