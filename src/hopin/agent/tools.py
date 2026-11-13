"""The tools HopIn offers an agent over MCP: each is the act of a person's own agent in a call."""

import json
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from typing import TYPE_CHECKING, Final

import structlog
from agents.member import (
    ACCEPT_INVITATION_TOOL,
    DECLINE_INVITATION_TOOL,
    SPEAK_TOOL,
    STOP_SPEAKING_TOOL,
    AcceptInvitationArguments,
    AcceptInvitationRefusal,
    AcceptInvitationRefused,
    DeclineInvitationArguments,
    DeclineInvitationReason,
    DeclineInvitationRefusal,
    DeclineInvitationRefused,
    PlaceRef,
    SpeakArguments,
    SpeakRefusal,
    SpeakRefused,
    Spoken,
    StoppedSpeaking,
    StopSpeakingArguments,
    StopSpeakingRefusal,
    StopSpeakingRefused,
)
from agents.tools import JsonObject, ToolDeclaration, ToolEffect, ToolOutcome
from pydantic import ValidationError

from hopin.agent.speaking import LanguageUnavailableError, StyleUnavailableError, known_voice
from hopin.participation.figures import Figure
from hopin.participation.participants import Caller, DialedAgent
from hopin.participation.text import one_line
from hopin.participation.vocabulary import (
    AGENT_LINE_MAX_CHARACTERS,
    AGENT_NAME_MAX_CHARACTERS,
    AgentRefusal,
    InvitationOutcome,
    SpeakStop,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from hopin.agent.speaking import AgentVoice
    from hopin.participation.feature import Participation

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

AGENT_VOICE_UNKNOWN_EVENT: Final = "agent_voice_unknown"

JOIN_CALL: Final = "join_call"
LEAVE_CALL: Final = "leave_call"
SAY: Final = "say"

# The longest sentence an agent speaks at once: the chat's line bound, since the sentence also stands there as a line.
SPEAK_MAX_CHARACTERS: Final = AGENT_LINE_MAX_CHARACTERS

# The kind of a place a call is, which the runtime's runner maps to a call's id.
CALL_KIND: Final = "call"

ROOM_DESCRIPTION: Final = "The call's id, the last part of its link."


class Audience(StrEnum):
    """The kind of agent a tool serves."""

    OWN_AGENT = "own_agent"
    OWN_ACCOUNT = "own_account"
    EVERY_AGENT = "every_agent"


# The one rule of which tool serves which kind of agent: a person's own agent acts through its person's seat, and an
# agent with an account of its own is invited and reads its standing.
AUDIENCE_OF_TOOL: Final = {
    JOIN_CALL: Audience.OWN_AGENT,
    LEAVE_CALL: Audience.EVERY_AGENT,
    ACCEPT_INVITATION_TOOL: Audience.OWN_ACCOUNT,
    DECLINE_INVITATION_TOOL: Audience.OWN_ACCOUNT,
    SAY: Audience.EVERY_AGENT,
    SPEAK_TOOL: Audience.EVERY_AGENT,
    STOP_SPEAKING_TOOL: Audience.EVERY_AGENT,
}

# The row's outcome a decline's reason shows to the seat that made the invitation.
_OUTCOME_OF_DECLINE: Final = {DeclineInvitationReason.BUSY: InvitationOutcome.BUSY}

_SPEAK_REFUSAL_OF: Final = {
    AgentRefusal.GONE: SpeakRefusal.NOT_ACTIVE,
    AgentRefusal.SPONSOR_AWAY: SpeakRefusal.NOT_ACTIVE,
    AgentRefusal.UNAVAILABLE: SpeakRefusal.UNAVAILABLE,
    SpeakStop.STOPPED: SpeakRefusal.STOPPED,
}

_STOP_REFUSAL_OF: Final = {
    AgentRefusal.GONE: StopSpeakingRefusal.NOT_ACTIVE,
    AgentRefusal.SPONSOR_AWAY: StopSpeakingRefusal.NOT_ACTIVE,
    SpeakStop.NOTHING_PLAYING: StopSpeakingRefusal.NOTHING_PLAYING,
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
            "room": {"type": "string", "description": ROOM_DESCRIPTION},
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
        "properties": {"room": {"type": "string", "description": ROOM_DESCRIPTION}},
        "required": ["room"],
        "additionalProperties": False,
    },
    effect=ToolEffect.SIDE_EFFECTING,
)

_ACCEPT_INVITATION_DECLARATION: Final = ToolDeclaration(
    name=ACCEPT_INVITATION_TOOL,
    description=(
        "Accept an invitation from your standing: you join the call as a participant of your own, under your name. "
        "Pass the standing's place and the invitation's id and epoch."
    ),
    parameters=AcceptInvitationArguments.model_json_schema(),
    effect=ToolEffect.SIDE_EFFECTING,
)
_DECLINE_INVITATION_DECLARATION: Final = ToolDeclaration(
    name=DECLINE_INVITATION_TOOL,
    description=(
        "Decline an invitation from your standing at once, when you are at your own limit of places: the person who "
        "called you sees that you are busy. Pass the standing's place, the invitation's id and epoch and the reason "
        "busy. It is refused as stale_invitation when the invitation is not yours or no longer stands."
    ),
    parameters=DeclineInvitationArguments.model_json_schema(),
    effect=ToolEffect.SIDE_EFFECTING,
)
_SAY_DECLARATION: Final = ToolDeclaration(
    name=SAY,
    description=(
        "Write a line in the chat of the call you joined. Everyone in the call reads it under your name with an AI "
        f"mark. A line holds 1 to {AGENT_LINE_MAX_CHARACTERS} characters with no line break, and you may write 10 "
        "lines a minute. Only those in the call now read the line; agents joined in it can read it in their bounded "
        "windows until it is evicted or their place ends."
    ),
    parameters={
        "type": "object",
        "properties": {
            "room": {"type": "string", "description": ROOM_DESCRIPTION},
            "text": {"type": "string", "description": "The line, as plain text."},
        },
        "required": ["room", "text"],
        "additionalProperties": False,
    },
    effect=ToolEffect.SIDE_EFFECTING,
)
_SPEAK_DECLARATION: Final = ToolDeclaration(
    name=SPEAK_TOOL,
    description=(
        "Say one sentence aloud in the call you joined. Everyone in the call hears it in your own voice and reads it "
        f"in the chat under your name with an AI mark. A sentence holds 1 to {SPEAK_MAX_CHARACTERS} characters. "
        "Sentences play one after another, each when the one before it has ended. The answer comes when yours starts, "
        "which can be after the ones before it have played, so allow for that in your timeout; it is how long yours "
        "plays. "
        "Name the language of the sentence as a two-letter code such as de or en; without one it is spoken in German. "
        "Name a style, such as louder and angrier, to have it spoken that way; the voice decides whether it can. "
        "It is refused as not_active when you are not joined there or your person's seat is away, as too_long above "
        "the bound, as language_unavailable when your voice does not speak the language, as style_unavailable when "
        "your voice refuses the style, as stopped when a stop_speaking dropped it while it waited and as unavailable "
        "when no voice can be made."
    ),
    parameters=SpeakArguments.model_json_schema(),
    effect=ToolEffect.SIDE_EFFECTING,
)
_STOP_SPEAKING_DECLARATION: Final = ToolDeclaration(
    name=STOP_SPEAKING_TOOL,
    description=(
        "Cut the sentence you are saying aloud in the call you joined, in every seat, and drop every sentence of yours "
        "that waits behind it; each of those speak calls answers stopped. You may speak again at once. It answers the "
        "text of the line the cut sentence left, nothing_playing when no sentence of yours plays and not_active when "
        "you are not joined there or your person's seat is away."
    ),
    parameters=StopSpeakingArguments.model_json_schema(),
    effect=ToolEffect.SIDE_EFFECTING,
)


@dataclass(frozen=True)
class CallerTool:
    """A tool and the handler that serves it to the caller the connector admitted."""

    declaration: ToolDeclaration
    handler: Callable[[Caller, JsonObject], Awaitable[ToolOutcome]]


def refused(refusal: AgentRefusal) -> ToolOutcome:
    return ToolOutcome(json.dumps({"refused": refusal.value}), is_error=True)


def _accept_refused(refusal: AcceptInvitationRefusal) -> ToolOutcome:
    return ToolOutcome(AcceptInvitationRefused(refused=refusal).model_dump_json(), is_error=True)


def _decline_refused(refusal: DeclineInvitationRefusal) -> ToolOutcome:
    return ToolOutcome(DeclineInvitationRefused(refused=refusal).model_dump_json(), is_error=True)


def _speak_refused(refusal: SpeakRefusal) -> ToolOutcome:
    return ToolOutcome(SpeakRefused(refused=refusal).model_dump_json(), is_error=True)


def _stop_refused(refusal: StopSpeakingRefusal) -> ToolOutcome:
    return ToolOutcome(StopSpeakingRefused(refused=refusal).model_dump_json(), is_error=True)


@dataclass(frozen=True)
class _SpeakRequest:
    place: PlaceRef
    text: str
    language: str | None
    style: str | None


def _speak_request(arguments: JsonObject) -> _SpeakRequest | None:
    """The place, the sentence, the language and the style a `speak` call names, or nothing when the arguments name no
    place. A sentence the schema refuses, a missing or an empty one, is no text, which participation answers once the
    place holds an agent; a language or a style it refuses is one no voice speaks or takes, which the voice answers once
    the sentence is rendered."""
    try:
        spoken = SpeakArguments.model_validate(arguments)
    except ValidationError as invalid:
        refused = {error["loc"][:1] for error in invalid.errors()}
        if not refused <= {("text",), ("language",), ("style",)}:
            return None
        return _SpeakRequest(
            PlaceRef.model_validate(arguments["place"]),
            "" if ("text",) in refused else _text_of(arguments, "text"),
            _text_of(arguments, "language") if ("language",) in refused else None,
            _text_of(arguments, "style") if ("style",) in refused else None,
        )
    return _SpeakRequest(spoken.place, spoken.text, spoken.language, spoken.style)


def _joined_or_refused(outcome: DialedAgent | AgentRefusal) -> ToolOutcome:
    if isinstance(outcome, DialedAgent):
        return ToolOutcome(json.dumps({"room": outcome.room_id, "name": outcome.name}), is_error=False)
    return refused(outcome)


def _text_of(arguments: JsonObject, name: str) -> str:
    value = arguments.get(name)
    return value if isinstance(value, str) else ""


async def _accepted(participation: Participation, caller: Caller, arguments: JsonObject) -> ToolOutcome:
    try:
        accepted = AcceptInvitationArguments.model_validate(arguments)
    except ValidationError:
        return _accept_refused(AcceptInvitationRefusal.STALE_INVITATION)
    if accepted.place.kind != CALL_KIND:
        return _accept_refused(AcceptInvitationRefusal.STALE_INVITATION)
    figure = None if accepted.figure is None else Figure(accepted.figure.url, accepted.figure.sha256)
    voice = known_voice(accepted.voice)
    outcome = await participation.accept(
        caller.person, accepted.place.id, accepted.invitation, accepted.epoch, figure, voice
    )
    if isinstance(outcome, AgentRefusal):
        return _accept_refused(AcceptInvitationRefusal(outcome.value))
    if accepted.voice is not None and voice is None:
        await logger.ainfo(AGENT_VOICE_UNKNOWN_EVENT, room_id=accepted.place.id)
    return _joined_or_refused(outcome)


async def _declined(participation: Participation, caller: Caller, arguments: JsonObject) -> ToolOutcome:
    try:
        declined = DeclineInvitationArguments.model_validate(arguments)
    except ValidationError:
        return _decline_refused(DeclineInvitationRefusal.STALE_INVITATION)
    if declined.place.kind != CALL_KIND:
        return _decline_refused(DeclineInvitationRefusal.STALE_INVITATION)
    refusal = await participation.decline(
        caller.person, declined.place.id, declined.invitation, declined.epoch, _OUTCOME_OF_DECLINE[declined.reason]
    )
    if refusal is not None:
        return _decline_refused(DeclineInvitationRefusal(refusal.value))
    return ToolOutcome(json.dumps({}), is_error=False)


async def _spoken(
    participation: Participation, voice: AgentVoice | None, caller: Caller, arguments: JsonObject
) -> ToolOutcome:
    request = _speak_request(arguments)
    if request is None or request.place.kind != CALL_KIND:
        return _speak_refused(SpeakRefusal.NOT_ACTIVE)
    if len(one_line(request.text)) > SPEAK_MAX_CHARACTERS:
        return _speak_refused(SpeakRefusal.TOO_LONG)
    render = (
        None if voice is None else partial(voice.render, caller.person, language=request.language, style=request.style)
    )
    try:
        outcome = await participation.speak(caller.person, request.place.id, request.text, render)
    except LanguageUnavailableError:
        return _speak_refused(SpeakRefusal.LANGUAGE_UNAVAILABLE)
    except StyleUnavailableError:
        return _speak_refused(SpeakRefusal.STYLE_UNAVAILABLE)
    if isinstance(outcome, AgentRefusal | SpeakStop):
        return _speak_refused(_SPEAK_REFUSAL_OF[outcome])
    return ToolOutcome(Spoken(duration_ms=outcome).model_dump_json(), is_error=False)


async def _stopped(participation: Participation, caller: Caller, arguments: JsonObject) -> ToolOutcome:
    try:
        stopping = StopSpeakingArguments.model_validate(arguments)
    except ValidationError:
        return _stop_refused(StopSpeakingRefusal.NOT_ACTIVE)
    if stopping.place.kind != CALL_KIND:
        return _stop_refused(StopSpeakingRefusal.NOT_ACTIVE)
    outcome = await participation.stop_speaking(caller.person, stopping.place.id)
    if isinstance(outcome, str):
        return ToolOutcome(StoppedSpeaking(cut=outcome).model_dump_json(), is_error=False)
    return _stop_refused(_STOP_REFUSAL_OF[outcome])


def participation_tools(participation: Callable[[], Participation], voice: AgentVoice | None) -> tuple[CallerTool, ...]:
    """The tools on the participation the call's handler builds; `participation` is read at each call. `voice` speaks
    an agent's sentences, none while speaking is off."""

    async def join_call(caller: Caller, arguments: JsonObject) -> ToolOutcome:
        outcome = await participation().dial_in(caller.person, _text_of(arguments, "room"), _text_of(arguments, "name"))
        return _joined_or_refused(outcome)

    async def accept_invitation(caller: Caller, arguments: JsonObject) -> ToolOutcome:
        return await _accepted(participation(), caller, arguments)

    async def decline_invitation(caller: Caller, arguments: JsonObject) -> ToolOutcome:
        return await _declined(participation(), caller, arguments)

    async def leave_call(caller: Caller, arguments: JsonObject) -> ToolOutcome:
        refusal = await participation().leave(caller.person, _text_of(arguments, "room"))
        if refusal is None:
            return ToolOutcome(json.dumps({"left": True}), is_error=False)
        return refused(refusal)

    async def say(caller: Caller, arguments: JsonObject) -> ToolOutcome:
        refusal = await participation().say(caller.person, _text_of(arguments, "room"), _text_of(arguments, "text"))
        if refusal is None:
            return ToolOutcome(json.dumps({}), is_error=False)
        return refused(refusal)

    async def speak(caller: Caller, arguments: JsonObject) -> ToolOutcome:
        return await _spoken(participation(), voice, caller, arguments)

    async def stop_speaking(caller: Caller, arguments: JsonObject) -> ToolOutcome:
        return await _stopped(participation(), caller, arguments)

    return (
        CallerTool(_JOIN_CALL_DECLARATION, join_call),
        CallerTool(_ACCEPT_INVITATION_DECLARATION, accept_invitation),
        CallerTool(_DECLINE_INVITATION_DECLARATION, decline_invitation),
        CallerTool(_LEAVE_CALL_DECLARATION, leave_call),
        CallerTool(_SAY_DECLARATION, say),
        CallerTool(_SPEAK_DECLARATION, speak),
        CallerTool(_STOP_SPEAKING_DECLARATION, stop_speaking),
    )
