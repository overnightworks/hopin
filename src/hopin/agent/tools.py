"""The tools HopIn offers an agent over MCP: each is the act of a person's own agent in a call."""

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from agent_runtime_client.tools import JsonObject, ToolDeclaration, ToolEffect, ToolOutcome

from hopin.participation.feature import Caller, DialedAgent
from hopin.participation.vocabulary import AGENT_NAME_MAX_CHARACTERS, AgentRefusal

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from hopin.participation.feature import Participation

JOIN_CALL: Final = "join_call"
LEAVE_CALL: Final = "leave_call"
ACCEPT_INVITATION: Final = "accept_invitation"
WAIT: Final = "wait"


class Audience(StrEnum):
    """The kind of agent a tool serves."""

    OWN_AGENT = "own_agent"
    OWN_ACCOUNT = "own_account"
    EVERY_AGENT = "every_agent"


# The one rule of which tool serves which kind of agent: a person's own agent acts through its person's seat, and an
# agent with an account of its own is invited and told its standing.
AUDIENCE_OF_TOOL: Final = {
    JOIN_CALL: Audience.OWN_AGENT,
    LEAVE_CALL: Audience.EVERY_AGENT,
    ACCEPT_INVITATION: Audience.OWN_ACCOUNT,
    WAIT: Audience.OWN_ACCOUNT,
}


def refusal_to_serve(tool: str, caller: Caller) -> AgentRefusal | None:
    """Why the tool does not serve this caller, or nothing when it does."""
    audience = AUDIENCE_OF_TOOL[tool]
    if audience is Audience.OWN_AGENT and caller.own_account is not None:
        return AgentRefusal.INVITATION_ONLY
    if audience is Audience.OWN_ACCOUNT and caller.own_account is None:
        return AgentRefusal.OWN_ACCOUNT_ONLY
    return None


_JOIN_CALL_DECLARATION: Final = ToolDeclaration(
    name=JOIN_CALL,
    description=(
        "Join the call you are in as a participant of your own. Everyone in the call sees a tile with the name "
        "you give and your person's name. It works only while your person holds exactly one live seat in the call, "
        "and you leave when that seat leaves. A repeat join returns the same agent and keeps the first name."
    ),
    parameters={
        "type": "object",
        "properties": {
            "room": {"type": "string", "description": "The call's id, the last part of its link."},
            "name": {
                "type": "string",
                "description": f"The name the call shows for you, 1 to {AGENT_NAME_MAX_CHARACTERS} characters.",
            },
        },
        "required": ["room", "name"],
        "additionalProperties": False,
    },
    effect=ToolEffect.SIDE_EFFECTING,
)
_LEAVE_CALL_DECLARATION: Final = ToolDeclaration(
    name=LEAVE_CALL,
    description="Leave the call you joined; your tile goes from the stage.",
    parameters={
        "type": "object",
        "properties": {"room": {"type": "string", "description": "The call's id, the last part of its link."}},
        "required": ["room"],
        "additionalProperties": False,
    },
    effect=ToolEffect.SIDE_EFFECTING,
)

_ACCEPT_INVITATION_DECLARATION: Final = ToolDeclaration(
    name=ACCEPT_INVITATION,
    description=(
        "Accept an invitation from your standings: you join the call as a participant of your own, under your name. "
        "Pass the standing's place id as room, and the invitation's id and epoch."
    ),
    parameters={
        "type": "object",
        "properties": {
            "room": {"type": "string", "description": "The place id of the standing."},
            "invitation": {"type": "string", "description": "The invitation's id."},
            "epoch": {"type": "integer", "minimum": 0, "description": "The invitation's epoch."},
        },
        "required": ["room", "invitation", "epoch"],
        "additionalProperties": False,
    },
    effect=ToolEffect.SIDE_EFFECTING,
)


@dataclass(frozen=True)
class CallerTool:
    """A tool and the handler that serves it to the caller the connector admitted."""

    declaration: ToolDeclaration
    handler: Callable[[Caller, JsonObject], Awaitable[ToolOutcome]]


def refused(refusal: AgentRefusal) -> ToolOutcome:
    return ToolOutcome(json.dumps({"refused": refusal.value}), is_error=True)


def _joined_or_refused(outcome: DialedAgent | AgentRefusal) -> ToolOutcome:
    if isinstance(outcome, DialedAgent):
        return ToolOutcome(json.dumps({"room": outcome.room_id, "name": outcome.name}), is_error=False)
    return refused(outcome)


def _text_of(arguments: JsonObject, name: str) -> str:
    value = arguments.get(name)
    return value if isinstance(value, str) else ""


def _epoch_of(arguments: JsonObject) -> int:
    epoch = arguments.get("epoch")
    return epoch if isinstance(epoch, int) and not isinstance(epoch, bool) else -1


def participation_tools(participation: Callable[[], Participation]) -> tuple[CallerTool, ...]:
    """The tools on the participation the call's handler builds; `participation` is read at each call."""

    async def join_call(caller: Caller, arguments: JsonObject) -> ToolOutcome:
        outcome = await participation().dial_in(caller.person, _text_of(arguments, "room"), _text_of(arguments, "name"))
        return _joined_or_refused(outcome)

    async def accept_invitation(caller: Caller, arguments: JsonObject) -> ToolOutcome:
        outcome = await participation().accept(
            caller.person, _text_of(arguments, "room"), _text_of(arguments, "invitation"), _epoch_of(arguments)
        )
        return _joined_or_refused(outcome)

    async def leave_call(caller: Caller, arguments: JsonObject) -> ToolOutcome:
        refusal = await participation().leave(caller.person, _text_of(arguments, "room"))
        if refusal is None:
            return ToolOutcome(json.dumps({"left": True}), is_error=False)
        return refused(refusal)

    return (
        CallerTool(_JOIN_CALL_DECLARATION, join_call),
        CallerTool(_ACCEPT_INVITATION_DECLARATION, accept_invitation),
        CallerTool(_LEAVE_CALL_DECLARATION, leave_call),
    )
