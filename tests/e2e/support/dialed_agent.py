import asyncio
import contextlib
import json
import secrets
import threading
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

import httpx2
from agents.member import ACCEPT_INVITATION_TOOL, STANDING_URI
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from playwright.sync_api import Page, expect

from hopin.agent.mcp import AGENT_GROUP, MCP_CLIENT_ID, MCP_GROUP, MCP_SCOPE
from hopin.agent.tools import JOIN_CALL, SAY
from hopin.config.constants import LOGIN_GROUP
from tests.e2e.support.calls import INVITE_LINK_PATTERN
from tests.e2e.support.people import PHONE

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from tests.e2e.support.server import RunningServer

OWNER_USERNAME = "owner"
OWNER_SUBJECT = f"subject-{OWNER_USERNAME}"
ADDERS_GROUP = "/apps/hopin/agent-adders"
AGENT_NAME = "Mozart"
AGENT_TILE = '#stage [id^="container-agent-"]'
REMOVE = ".agent-remove"
COUNT = "#participantCount .num"
REQUEST_SECONDS = 30
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


def room_of_link(link: str) -> str:
    found = INVITE_LINK_PATTERN.search(link)
    assert found is not None
    return found.group(0).removeprefix("/c/")


class Agent:
    """A person's own agent, calling HopIn's MCP endpoint with a token its client would hold."""

    def __init__(self, server: RunningServer, person: Page, member_of: Callable[..., Any]):
        self._room = room_of_link(person.url)
        self._agent = OwnAgent(server, member_of(person).account.id)

    def dial_in(self, name: str) -> None:
        self._agent.dial_in(self._room, name)

    def say(self, text: str) -> None:
        self._agent.say(self._room, text)

    def close(self) -> None:
        self._agent.close()


class SdkAgent:
    """A scripted client on the official SDK that calls HopIn's MCP endpoint with a token as the runner does. The SDK
    runs on an event loop of its own thread, so the browser keeps its owning thread; `close` ends the stream and joins
    the thread."""

    def __init__(self, server: RunningServer, token: str):
        self._url = f"{server.base_url}/mcp"
        self._token = token
        self._loop = asyncio.new_event_loop()
        self._opened = threading.Event()
        self._acknowledged = threading.Event()
        self._woken = threading.Event()
        self._stopping = asyncio.Event()
        self._client: Client
        self._stream: asyncio.Task[None] | None = None
        self._thread = threading.Thread(target=self._loop.run_until_complete, args=(self._hold_the_client(),))
        self._thread.start()
        if not self._opened.wait(REQUEST_SECONDS):
            self.close()
            message = "the SDK client did not open"
            raise TimeoutError(message)

    async def _hold_the_client(self) -> None:
        http = httpx2.AsyncClient(
            headers={"authorization": f"Bearer {self._token}"},
            timeout=httpx2.Timeout(REQUEST_SECONDS, read=None),
            verify=False,  # the loopback certificate is self-signed
            trust_env=False,
        )
        async with AsyncExitStack() as held:
            # An opening that never finishes ends the thread, which `close` joins.
            async with asyncio.timeout(REQUEST_SECONDS):
                await held.enter_async_context(http)
                self._client = await held.enter_async_context(
                    Client(streamable_http_client(self._url, http_client=http))
                )
            self._opened.set()
            await self._stopping.wait()

    async def _listen_for_wakes(self, uris: Sequence[str]) -> None:
        async with self._client.listen(resource_subscriptions=list(uris)) as stream:
            self._acknowledged.set()
            async for _event in stream:
                self._woken.set()

    @staticmethod
    async def _stop(stream: asyncio.Task[None]) -> None:
        """Cancel the old stream and wait until its task has ended, so it can no longer wake the agent."""
        stream.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stream

    async def _start(self, uris: Sequence[str]) -> asyncio.Task[None]:
        return asyncio.create_task(self._listen_for_wakes(uris))

    def _run(self, coroutine: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result(REQUEST_SECONDS)

    def listen(self, uris: Sequence[str] = (STANDING_URI,)) -> None:
        """Replace the stream with one on the resources and return once the server has acknowledged it."""
        if self._stream is not None:
            self._run(self._stop(self._stream))
        self._acknowledged.clear()
        self._woken.clear()
        self._stream = self._run(self._start(uris))
        assert self._acknowledged.wait(REQUEST_SECONDS)

    def read(self, uri: str) -> dict[str, Any]:
        return json.loads(self._run(self._client.read_resource(uri)).contents[0].text)

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._run(self._client.call_tool(tool, arguments))
        text = result.content[0].text
        assert not result.is_error, text
        return json.loads(text)

    def woken_within(self, seconds: float) -> bool:
        """Whether the stream has woken the agent, waiting up to `seconds` for it."""
        return self._woken.wait(seconds)

    def forget_wakes(self) -> None:
        self._woken.clear()

    def close(self) -> None:
        if self._stream is not None:
            self._loop.call_soon_threadsafe(self._stream.cancel)
        self._loop.call_soon_threadsafe(self._stopping.set)
        self._thread.join(REQUEST_SECONDS)
        self._loop.close()


class OwnAccountAgent(SdkAgent):
    """An agent with an account of its own, signed in with a client-credentials shaped token."""

    def __init__(self, server: RunningServer, owner_subject: str, name: str):
        self.name = name
        token = server.issuer.access_token(
            f"agent-of-{owner_subject}-{secrets.token_hex(4)}",
            audience=f"{server.base_url}/mcp",
            authorized_party="some-agent-client",
            scope=MCP_SCOPE,
            groups=(AGENT_GROUP, MCP_GROUP),
            extra_claims={"agent_owner": owner_subject, "agent_name": name},
        )
        super().__init__(server, token)

    def standings(self) -> list[dict[str, Any]]:
        return self.read(STANDING_URI)["standings"]

    def invitation_within(self, seconds: int) -> dict[str, Any]:
        """The standing that holds an invitation, once the stream has woken the agent."""
        assert self.woken_within(seconds), "the stream never woke the agent"
        [invited] = [standing for standing in self.standings() if standing["state"] == "invited"]
        return invited

    def accept(self, standing: dict[str, Any]) -> dict[str, Any]:
        invitation = standing["invitation"]
        arguments = {"place": standing["place"], "invitation": invitation["id"], "epoch": invitation["epoch"]}
        return self.call(ACCEPT_INVITATION_TOOL, arguments)


class OwnAgent(SdkAgent):
    """A person's own agent, signed in with the person's token as the client does that holds it."""

    def __init__(self, server: RunningServer, account_id: str):
        token = server.issuer.access_token(
            account_id,
            audience=f"{server.base_url}/mcp",
            authorized_party=MCP_CLIENT_ID,
            scope=MCP_SCOPE,
            groups=(LOGIN_GROUP, MCP_GROUP),
        )
        super().__init__(server, token)

    def dial_in(self, room: str, name: str) -> dict[str, Any]:
        return self.call(JOIN_CALL, {"room": room, "name": name})

    def say(self, room: str, text: str) -> dict[str, Any]:
        return self.call(SAY, {"room": room, "text": text})


def assert_tiles_clear_of_the_controls(person: Page, tiles: int, form_factor: dict[str, Any]) -> None:
    """A phone shows one or two tiles full-bleed under the controls, so there only the names must clear them."""
    expect(person.locator("#stage")).to_have_attribute("data-count", str(tiles))
    full_bleed = form_factor is PHONE and tiles <= FULL_BLEED_TILES
    overhang = person.evaluate(LABELS_ABOVE_THE_CONTROLS if full_bleed else TILES_ABOVE_THE_CONTROLS)
    assert len(overhang) == (1 if full_bleed else tiles)
    assert all(extra <= TOLERANCE_PIXELS for extra in overhang), overhang
