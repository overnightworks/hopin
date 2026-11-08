"""The tools HopIn offers an agent over MCP: each is the act of a person's own agent in a call."""

import json
from typing import TYPE_CHECKING, Final

from agent_runtime_client.tools import HandTool, JsonObject, Principal, ToolDeclaration, ToolEffect, ToolOutcome

from hopin.participation.feature import DialedAgent, Person
from hopin.participation.vocabulary import AGENT_NAME_MAX_CHARACTERS, AgentRefusal

if TYPE_CHECKING:
    from collections.abc import Callable

    from hopin.participation.feature import Participation

JOIN_CALL: Final = "join_call"
LEAVE_CALL: Final = "leave_call"

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


def _person_of(principal: Principal) -> Person:
    return Person(principal.iss, principal.sub)


def _refused(refusal: AgentRefusal) -> ToolOutcome:
    return ToolOutcome(json.dumps({"refused": refusal.value}), is_error=True)


def _text_of(arguments: JsonObject, name: str) -> str:
    value = arguments.get(name)
    return value if isinstance(value, str) else ""


def participation_tools(participation: Callable[[], Participation]) -> tuple[HandTool, ...]:
    """The tools on the participation the call's handler builds; `participation` is read at each call."""

    async def join_call(principal: Principal, arguments: JsonObject) -> ToolOutcome:
        outcome = await participation().dial_in(
            _person_of(principal), _text_of(arguments, "room"), _text_of(arguments, "name")
        )
        if isinstance(outcome, DialedAgent):
            return ToolOutcome(json.dumps({"room": outcome.room_id, "name": outcome.name}), is_error=False)
        return _refused(outcome)

    async def leave_call(principal: Principal, arguments: JsonObject) -> ToolOutcome:
        refusal = await participation().leave(_person_of(principal), _text_of(arguments, "room"))
        if refusal is None:
            return ToolOutcome(json.dumps({"left": True}), is_error=False)
        return _refused(refusal)

    return (HandTool(_JOIN_CALL_DECLARATION, join_call), HandTool(_LEAVE_CALL_DECLARATION, leave_call))
