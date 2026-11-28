import json
import secrets
import threading
from contextlib import ExitStack, asynccontextmanager
from functools import partial
from typing import TYPE_CHECKING, Any

import httpx2
from agents.mcp_client import MemberSession, ResourceWake, open_member_session
from agents.mcp_wire import MCP_ENDPOINT_PATH
from agents.member import (
    GESTURE_TOOL,
    SET_FRAMING_TOOL,
    STANDING_URI,
    AcceptInvitationArguments,
    DeclineInvitationArguments,
    DeclineInvitationReason,
    PlaceRef,
    PlaceWindow,
    SetTurnStateArguments,
    Standing,
    StandingState,
    place_window_uri,
)
from agents.member_outcomes import DeclineOutcome
from anyio.from_thread import start_blocking_portal
from design.words import text as kit_text
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.subscriptions import ResourceUpdated
from playwright.sync_api import Locator, Page, expect
from pydantic import SecretStr

from hopin.agent.tools import CALL_KIND, JOIN_CALL, LOOK, SAY
from tests.e2e.support.calls import INVITE_LINK_PATTERN
from tests.e2e.support.people import PHONE
from tests.support.sign_in_settings import TEST_REALM

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from concurrent.futures import Future
    from contextlib import AbstractAsyncContextManager

    from agents.member import (
        AcceptInvitationRefusal,
        SpeakAnswer,
        SpeakRefusal,
    )
    from agents.member_outcomes import UnrecognisedRefusal
    from agents.tools import ToolOutcome
    from anyio.abc import TaskStatus

    from tests.e2e.support.server import RunningServer

OWNER_USERNAME = "owner"
OWNER_SUBJECT = f"subject-{OWNER_USERNAME}"
AGENT_NAME = "Mozart"
SECOND_AGENT_NAME = "Leonardo"
AGENT_TILE = '#stage [id^="container-agent-"]'
# The tile holds the kit's hearing, listen and see chips; each is told by its accessible name.
SEE_SWITCH_ON_A_TILE = f'role=switch[name="{kit_text("de", "hearing.agentMaySeeMe")}"]'
HEARING_SWITCH_ON_A_TILE = f'role=switch[name="{kit_text("de", "hearing.agentMayHearMe")}"]'
LISTEN_SWITCH_ON_A_TILE = f'role=switch[name="{kit_text("de", "hearing.iWantToHearAgent")}"]'
AGENT_HEARING_SWITCH = f"{AGENT_TILE} >> {HEARING_SWITCH_ON_A_TILE}"
AGENT_LISTEN_SWITCH = f"{AGENT_TILE} >> {LISTEN_SWITCH_ON_A_TILE}"
AGENT_SEE_SWITCH = f"{AGENT_TILE} >> {SEE_SWITCH_ON_A_TILE}"
TURN_STATE_ON_A_TILE = f"{AGENT_TILE} .turn-state"
NAME_PILL_ON_A_TILE = f"{AGENT_TILE} .name"
# A row of the agent control in More: the agents a seat may send away as here, then those it may call.
OFFER = "#agentOffers li"
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


def send_away_button(person: Page, agent_name: str) -> Locator:
    """The button that sends the agent away on its here row in More, which is open on `person`'s page."""
    return (
        person.locator(OFFER)
        .filter(has_text=agent_name)
        .get_by_role("button", name=kit_text("de", "agent.askToLeaveLabel", name=agent_name))
    )


def room_of_link(link: str) -> str:
    found = INVITE_LINK_PATTERN.search(link)
    assert found is not None
    return found.group(0).removeprefix("/c/")


def refusal_of(answer: ToolOutcome) -> str:
    """The code a refused tool call answers with."""
    assert answer.is_error
    return json.loads(answer.content)["refused"]


def call_place(room: str) -> PlaceRef:
    return PlaceRef(kind=CALL_KIND, id=room)


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


class PortalAgent[Session]:
    """A scripted client of HopIn's MCP endpoint whose session runs on the event loop of a blocking portal, so the
    browser keeps its owning thread; `close` ends the stream and the session."""

    def __init__(self, opened: AbstractAsyncContextManager[Session]):
        self._woken_by: set[str] = set()
        self._woken = threading.Condition()
        self._hearing: Future[None] | None = None
        self._pending: list[Future[Any]] = []
        self._stack = ExitStack()
        try:
            self._portal = self._stack.enter_context(start_blocking_portal())
            self._session = self._stack.enter_context(self._portal.wrap_async_context_manager(opened))
        except BaseException:
            self._stack.close()
            raise

    def _later[Answer](self, call: Callable[..., Awaitable[Answer]], *arguments: Any) -> Future[Answer]:
        """Start the session's call and return at once with its answer to come, for a tool that answers later while the
        test keeps driving its browsers: a thread waiting on the answer serves none of their page routes."""
        future = self._portal.start_task_soon(call, *arguments)
        self._pending.append(future)
        return future

    def _run[Answer](self, call: Callable[..., Awaitable[Answer]], *arguments: Any) -> Answer:
        return self._later(call, *arguments).result(REQUEST_SECONDS)

    def _hear_with(self, hear: Callable[..., Awaitable[None]]) -> None:
        """Replace the stream with `hear` and return once it has started, which the server has acknowledged."""
        self.stop_listening()
        self.forget_wakes()
        self._hearing, _ = self._portal.start_task(hear)

    def _woke_by(self, uri: str) -> None:
        with self._woken:
            self._woken_by.add(uri)
            self._woken.notify_all()

    def stop_listening(self) -> None:
        """End the stream and keep the session: the server counts the agent as listening until its allowance passes."""
        if self._hearing is not None:
            self._hearing.cancel()
            self._hearing = None

    def woken_by_within(self, uri: str, seconds: float) -> bool:
        """Whether the stream has woken the agent for the resource at `uri`, waiting up to `seconds` for it."""
        with self._woken:
            return self._woken.wait_for(lambda: uri in self._woken_by, seconds)

    def woken_by_window_within(self, room: str, seconds: float) -> bool:
        return self.woken_by_within(place_window_uri(call_place(room)), seconds)

    def forget_wakes(self) -> None:
        with self._woken:
            self._woken_by.clear()

    def close(self) -> None:
        self.stop_listening()
        for future in self._pending:
            future.cancel()
        self._stack.close()


class DialedMember(PortalAgent[MemberSession]):
    """A scripted member of HopIn's MCP endpoint that holds a token as the runner does, through the agents library's
    member session."""

    def __init__(self, server: RunningServer, token: str):
        super().__init__(
            open_member_session(
                f"{server.base_url}{MCP_ENDPOINT_PATH}",
                lambda: SecretStr(token),
                # The loopback certificate is self-signed.
                transport=httpx2.AsyncHTTPTransport(verify=False),
            )
        )

    async def _hear(self, *, task_status: TaskStatus[None]) -> None:
        async with self._session.listen() as wakes:
            task_status.started()
            async for wake in wakes:
                if isinstance(wake, ResourceWake):
                    self._woke_by(wake.uri)

    def listen(self) -> None:
        """Replace the stream with one on the standing and return once the server has acknowledged it."""
        self._hear_with(self._hear)

    def woken_within(self, seconds: float) -> bool:
        """Whether the stream has woken the agent for any resource, waiting up to `seconds` for it; a notice that the
        agent's tools changed wakes no resource."""
        with self._woken:
            return self._woken.wait_for(lambda: bool(self._woken_by), seconds)

    def tools_notices(self) -> int:
        return self._session.tools_notices()

    def standings(self) -> tuple[Standing, ...]:
        return self._run(self._session.read_standing).standings

    def window(self, room: str) -> PlaceWindow:
        window = self._run(self._session.read_place, call_place(room))
        assert window is not None, "the app shows the agent no window of the call"
        return window

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        outcome = self.invoke(tool, arguments)
        assert not outcome.is_error, outcome.content
        return json.loads(outcome.content)

    def invoke(self, tool: str, arguments: dict[str, Any]) -> ToolOutcome:
        return self._run(self._session.invoke, tool, arguments)

    def refusal_of(self, tool: str, arguments: dict[str, Any]) -> str:
        """Call the tool and return the code the call refuses it with."""
        return refusal_of(self.invoke(tool, arguments))

    def look_unawaited(self, room: str) -> Future[ToolOutcome]:
        """Start the agent's `look` in the room and return its outcome to come, since the answer is a picture and not
        the JSON of the other tools; the sponsor's browser answers while the test keeps driving its pages."""
        return self._later(self._session.invoke, LOOK, {"place": call_place(room).model_dump()})

    def speak(self, room: str, text: str, interrupted: str | None = None) -> SpeakAnswer | SpeakRefusal:
        return self.speak_unawaited(room, text, interrupted).result(REQUEST_SECONDS)

    def speak_unawaited(
        self, room: str, text: str, interrupted: str | None = None
    ) -> Future[SpeakAnswer | SpeakRefusal]:
        """Start the sentence and return at once with its answer to come."""
        return self._later(self._session.speak, call_place(room), text, None, interrupted)


class OwnAccountAgent(DialedMember):
    """An agent with an account of its own, signed in with a client-credentials shaped token."""

    def __init__(self, server: RunningServer, owner_subject: str, name: str):
        self.name = name
        token = server.issuer.access_token(
            f"agent-of-{owner_subject}-{secrets.token_hex(4)}",
            audience=f"{server.base_url}{MCP_ENDPOINT_PATH}",
            authorized_party="some-agent-client",
            scope=TEST_REALM.mcp_scope,
            groups=(str(TEST_REALM.agent_group), str(TEST_REALM.mcp_group)),
            extra_claims={"agent_owner": owner_subject, "agent_name": name},
        )
        super().__init__(server, token)

    def invitation_within(self, seconds: int) -> Standing:
        """The standing that holds an invitation, once the stream has woken the agent."""
        assert self.woken_by_within(STANDING_URI, seconds), "the stream never woke the agent"
        [invited] = [standing for standing in self.standings() if standing.state is StandingState.INVITED]
        return invited

    def accept(self, standing: Standing, **named: Any) -> None:
        """Accept the invitation, naming the `figure` (`url` and `sha256`) its tile shows if it has one, the `voices`
        it would speak with, first choice first, and its `looks`."""
        refusal = self.accept_refusal(standing, **named)
        assert refusal is None, refusal

    def accept_refusal(self, standing: Standing, **named: Any) -> AcceptInvitationRefusal | UnrecognisedRefusal | None:
        """Accept the invitation and return the code the call refuses it with, none when it joins."""
        invitation = standing.invitation
        assert invitation is not None
        arguments = {"place": standing.place, "invitation": invitation.id, "epoch": invitation.epoch} | {
            name: value for name, value in named.items() if value is not None
        }
        return self._run(self._session.accept, AcceptInvitationArguments.model_validate(arguments, strict=False))

    def active_place(self) -> PlaceRef:
        """The one place the agent holds."""
        [active] = [standing for standing in self.standings() if standing.state is StandingState.ACTIVE]
        return active.place

    def gesture(self, name: str) -> dict[str, Any]:
        """Make the gesture with the figure at the one place the agent holds."""
        return self.call(GESTURE_TOOL, {"place": self.active_place().model_dump(), "gesture": name})

    def set_framing(self, name: str) -> dict[str, Any]:
        """Take the framing at the one place the agent holds."""
        return self.call(SET_FRAMING_TOOL, {"place": self.active_place().model_dump(), "framing": name})

    def state_turn(self, **statement: Any) -> None:
        """State the turn at the one place the agent holds: `thinking`, why its turns `stopped`, or none of both."""
        refusal = self._run(
            self._session.set_turn_state,
            SetTurnStateArguments.model_validate({"place": self.active_place()} | statement, strict=False),
        )
        assert refusal is None, refusal

    def decline_busy(self, standing: Standing) -> None:
        """Decline the invitation because the agent is at its own limit of places."""
        invitation = standing.invitation
        assert invitation is not None
        declined = self._run(
            self._session.decline,
            DeclineInvitationArguments(
                place=standing.place,
                invitation=invitation.id,
                epoch=invitation.epoch,
                reason=DeclineInvitationReason.BUSY,
            ),
        )
        assert declined is DeclineOutcome.SENT, declined


@asynccontextmanager
async def _plain_client(url: str, token: str) -> AsyncIterator[Client]:
    http = httpx2.AsyncClient(
        headers={"authorization": f"Bearer {token}"},
        timeout=httpx2.Timeout(REQUEST_SECONDS, read=None),
        verify=False,  # the loopback certificate is self-signed
        trust_env=False,
    )
    async with http, Client(streamable_http_client(url, http_client=http)) as client:
        yield client


class OwnAgent(PortalAgent[Client]):
    """A person's own agent, signed in with the person's token as the client does that holds it. It stays on the plain
    MCP client: it is that client and never the runner, and it listens on its window's URI, as the app offers the
    standing only to an agent with an account of its own."""

    def __init__(self, server: RunningServer, account_id: str):
        token = server.issuer.access_token(
            account_id,
            audience=f"{server.base_url}{MCP_ENDPOINT_PATH}",
            authorized_party=TEST_REALM.mcp_client_id,
            scope=TEST_REALM.mcp_scope,
            groups=(str(TEST_REALM.login_group), str(TEST_REALM.mcp_group)),
        )
        super().__init__(_plain_client(f"{server.base_url}{MCP_ENDPOINT_PATH}", token))

    async def _hear(self, room: str, *, task_status: TaskStatus[None]) -> None:
        window = place_window_uri(call_place(room))
        async with self._session.listen(resource_subscriptions=[window]) as events:
            task_status.started()
            async for event in events:
                if isinstance(event, ResourceUpdated):
                    self._woke_by(event.uri)

    def listen(self, room: str) -> None:
        """Replace the stream with one on the room's window and return once the server has acknowledged it."""
        self._hear_with(partial(self._hear, room))

    def window(self, room: str) -> PlaceWindow:
        read = self._run(self._session.read_resource, place_window_uri(call_place(room)))
        return PlaceWindow.model_validate_json(read.contents[0].text)

    def dial_in(self, room: str, name: str) -> None:
        self._call(JOIN_CALL, {"room": room, "name": name})

    def say(self, room: str, text: str) -> None:
        self._call(SAY, {"room": room, "text": text})

    def _call(self, tool: str, arguments: dict[str, Any]) -> None:
        result = self._run(self._session.call_tool, tool, arguments)
        assert not result.is_error, result.content


def assert_tiles_clear_of_the_controls(person: Page, tiles: int, form_factor: dict[str, Any]) -> None:
    """A phone shows one or two tiles full-bleed under the controls, so there only the names must clear them."""
    expect(person.locator("#stage")).to_have_attribute("data-count", str(tiles))
    full_bleed = form_factor is PHONE and tiles <= FULL_BLEED_TILES
    overhang = person.evaluate(LABELS_ABOVE_THE_CONTROLS if full_bleed else TILES_ABOVE_THE_CONTROLS)
    assert len(overhang) == (1 if full_bleed else tiles)
    assert all(extra <= TOLERANCE_PIXELS for extra in overhang), overhang


def lies_inside(inner: dict[str, float], outer: dict[str, float]) -> bool:
    return (
        inner["x"] >= outer["x"] - TOLERANCE_PIXELS
        and inner["y"] >= outer["y"] - TOLERANCE_PIXELS
        and inner["x"] + inner["width"] <= outer["x"] + outer["width"] + TOLERANCE_PIXELS
        and inner["y"] + inner["height"] <= outer["y"] + outer["height"] + TOLERANCE_PIXELS
    )


def share_area(first: dict[str, float], second: dict[str, float]) -> float:
    """The area two boxes share."""
    width = min(first["x"] + first["width"], second["x"] + second["width"]) - max(first["x"], second["x"])
    height = min(first["y"] + first["height"], second["y"] + second["height"]) - max(first["y"], second["y"])
    return max(width, 0) * max(height, 0)


def assert_the_pill_is_whole_and_clear_of_the_chips(person: Page, chips: tuple[str, ...], *, word_shown: bool) -> None:
    """The agent's name pill lies inside its tile, a stated turn state's word, when the seat shows one, lies inside the
    pill, and none of the chips the seat must show overlaps the pill."""
    tile_box = person.locator(AGENT_TILE).bounding_box()
    pill_box = person.locator(NAME_PILL_ON_A_TILE).bounding_box()
    assert tile_box is not None
    assert pill_box is not None
    assert lies_inside(pill_box, tile_box)
    if word_shown:
        word_box = person.locator(TURN_STATE_ON_A_TILE).bounding_box()
        assert word_box is not None
        assert lies_inside(word_box, pill_box)
    for chip in chips:
        chip_box = person.locator(chip).bounding_box()
        assert chip_box is not None
        assert share_area(chip_box, pill_box) == 0
