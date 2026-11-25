"""The tools HopIn offers an agent over MCP: each is the act of a person's own agent in a call.

`join_call`, `accept_invitation` and `decline_invitation` have the runtime's shared shape; `leave_call`, `say` and
`speak` follow. The quiz agent never gets `say` or `speak`. `AUDIENCE_OF_TOOL` is the one table of which tool serves
which kind of agent.
"""

import json
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from typing import TYPE_CHECKING, Final

import structlog
from agents.member import (
    ACCEPT_INVITATION_TOOL,
    CONTINUE_SPEAKING_TOOL,
    DECLINE_INVITATION_TOOL,
    GESTURE_TOOL,
    SET_FRAMING_TOOL,
    SET_TURN_STATE_TOOL,
    SPEAK_TOOL,
    STOP_SPEAKING_TOOL,
    AcceptInvitationArguments,
    AcceptInvitationRefusal,
    AcceptInvitationRefused,
    ContinuedSpeaking,
    ContinueSpeakingArguments,
    ContinueSpeakingRefusal,
    ContinueSpeakingRefused,
    DeclineInvitationArguments,
    DeclineInvitationReason,
    DeclineInvitationRefusal,
    DeclineInvitationRefused,
    DroppedSpeaking,
    FigureLook,
    FigureRef,
    GestureArguments,
    GestureRefusal,
    GestureRefused,
    HttpsFileRef,
    PlaceRef,
    Relayed,
    SetFramingArguments,
    SetFramingRefusal,
    SetFramingRefused,
    SetTurnStateArguments,
    SetTurnStateRefusal,
    SetTurnStateRefused,
    SpeakArguments,
    SpeakRefusal,
    SpeakRefused,
    Spoken,
    StoppedSpeaking,
    StopSpeakingArguments,
    StopSpeakingRefusal,
    StopSpeakingRefused,
    Written,
    framing_tool,
    gesture_tool,
    member_arguments,
)
from agents.tools import JsonObject, ToolDeclaration, ToolEffect, ToolImage, ToolOutcome, tool_parameters
from pydantic import BaseModel, ConfigDict, ValidationError

from hopin.agent.speaking import LanguageUnavailableError, StyleUnavailableError, known_voices
from hopin.config.constants import LINE_LIMITS
from hopin.participation.figures import Clip, Figure, PersonaRoom
from hopin.participation.participants import Caller, DialedAgent
from hopin.participation.text import one_line
from hopin.participation.vocabulary import (
    AGENT_NAME_MAX_CHARACTERS,
    LOOKS_PER_WINDOW,
    AgentRefusal,
    FigureFraming,
    FigureGesture,
    FigureMood,
    HoldRefusal,
    InvitationOutcome,
    LookRefusal,
    SpeakFallback,
    SpeakStop,
    TurnOutcome,
)
from hopin.rooms.reference import room_reference

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from hopin.agent.speaking import AgentVoice
    from hopin.participation.feature import Participation

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


JOIN_CALL: Final = "join_call"
LEAVE_CALL: Final = "leave_call"
SAY: Final = "say"
LOOK: Final = "look"

# The longest sentence an agent speaks at once: the chat's line bound, since the sentence also stands there as a line.
SPEAK_MAX_CHARACTERS: Final = LINE_LIMITS.max_characters

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
    CONTINUE_SPEAKING_TOOL: Audience.EVERY_AGENT,
    GESTURE_TOOL: Audience.EVERY_AGENT,
    SET_FRAMING_TOOL: Audience.EVERY_AGENT,
    SET_TURN_STATE_TOOL: Audience.EVERY_AGENT,
    LOOK: Audience.EVERY_AGENT,
}

# The row's outcome a decline's reason shows to the seat that made the invitation.
_OUTCOME_OF_DECLINE: Final = {DeclineInvitationReason.BUSY: InvitationOutcome.BUSY}

_SPEAK_REFUSAL_OF: Final = {
    AgentRefusal.GONE: SpeakRefusal.NOT_ACTIVE,
    AgentRefusal.SPONSOR_AWAY: SpeakRefusal.NOT_ACTIVE,
    AgentRefusal.UNAVAILABLE: SpeakRefusal.UNAVAILABLE,
    SpeakStop.STOPPED: SpeakRefusal.STOPPED,
    HoldRefusal.HELD: SpeakRefusal.HELD,
    HoldRefusal.NOT_INTERRUPTED: SpeakRefusal.NOT_INTERRUPTED,
}

_STOP_REFUSAL_OF: Final = {
    AgentRefusal.GONE: StopSpeakingRefusal.NOT_ACTIVE,
    AgentRefusal.SPONSOR_AWAY: StopSpeakingRefusal.NOT_ACTIVE,
    SpeakStop.NOTHING_PLAYING: StopSpeakingRefusal.NOTHING_PLAYING,
    HoldRefusal.NOT_INTERRUPTED: StopSpeakingRefusal.NOT_INTERRUPTED,
}

_CONTINUE_REFUSAL_OF: Final = {
    AgentRefusal.GONE: ContinueSpeakingRefusal.NOT_ACTIVE,
    AgentRefusal.SPONSOR_AWAY: ContinueSpeakingRefusal.NOT_ACTIVE,
    HoldRefusal.NOT_INTERRUPTED: ContinueSpeakingRefusal.NOT_INTERRUPTED,
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
    parameters=tool_parameters(AcceptInvitationArguments),
    effect=ToolEffect.SIDE_EFFECTING,
)
_DECLINE_INVITATION_DECLARATION: Final = ToolDeclaration(
    name=DECLINE_INVITATION_TOOL,
    description=(
        "Decline an invitation from your standing at once, when you are at your own limit of places: the person who "
        "called you sees that you are busy. Pass the standing's place, the invitation's id and epoch and the reason "
        "busy. It is refused as stale_invitation when the invitation is not yours or no longer stands."
    ),
    parameters=tool_parameters(DeclineInvitationArguments),
    effect=ToolEffect.SIDE_EFFECTING,
)
_SAY_DECLARATION: Final = ToolDeclaration(
    name=SAY,
    description=(
        "Write a line in the chat of the call you joined. Everyone in the call reads it under your name with an AI "
        f"mark. A line holds 1 to {LINE_LIMITS.max_characters} characters with no line break. "
        "Only those in the call now read the line; agents joined in it can read it in their bounded "
        "windows until it is evicted or their place ends. A written line is read, not heard: it stands in the chat, "
        "where it can be read again and kept, so it suits links, lists and names."
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
        "your voice refuses the style, as stopped when a stop_speaking or a speak naming your hold dropped it while it "
        "waited and as unavailable when speaking is off or not allowed to you or you have reached a limit. While you "
        "are held, because a person started speaking over you, nothing of yours plays or starts; a speak that does "
        "not name the hold's id as interrupted is refused as held, one that names an id that is not the hold is "
        "refused as not_interrupted, and one that names it drops what was held and plays at once; "
        "continue_speaking goes on with what was held instead. When the voice "
        "fails, the sentence is "
        "written in the chat as your line instead, after the sentences before it have played, and the answer is "
        "written: true; the window's voice_state says whether your voice works there."
    ),
    parameters=tool_parameters(SpeakArguments),
    effect=ToolEffect.SIDE_EFFECTING,
)
_STOP_SPEAKING_DECLARATION: Final = ToolDeclaration(
    name=STOP_SPEAKING_TOOL,
    description=(
        "Cut the sentence you are saying aloud in the call you joined, in every seat, and drop every sentence of yours "
        "that waits behind it; each of those speak calls answers stopped. You may speak again at once. It answers the "
        "text of the line the cut sentence left, dropped: true when no sentence had started that it cut, as when "
        "you were held before yours started, nothing_playing when no sentence of yours plays and not_active when "
        "you are not joined there or your person's seat is away. It ends a hold, whose id you may name as "
        "interrupted; an id that is not the hold is refused as not_interrupted."
    ),
    parameters=tool_parameters(StopSpeakingArguments),
    effect=ToolEffect.SIDE_EFFECTING,
)
_CONTINUE_SPEAKING_DECLARATION: Final = ToolDeclaration(
    name=CONTINUE_SPEAKING_TOOL,
    description=(
        "Go on with what you were saying after a person started speaking over you and held you: name the hold's id, "
        "which your window states as interrupted. The sentence that was held plays on, where it paused, faded back in "
        "in every seat, then the sentences of yours that waited behind it, and the hold ends. It is refused as "
        "not_interrupted when no hold stands or the id is another one and as not_active when you are not joined there "
        "or your person's seat is away."
    ),
    parameters=tool_parameters(ContinueSpeakingArguments),
    effect=ToolEffect.SIDE_EFFECTING,
)

_SET_TURN_STATE_DECLARATION: Final = ToolDeclaration(
    name=SET_TURN_STATE_TOOL,
    description=(
        "State at the call you joined whether a turn of yours is running: thinking true when one starts, and after "
        "it ends none, or why your turns there stop. While you think, your figure shows it to everyone in the call, "
        "until your next speak, gesture or statement there, or two minutes. It is refused as not_held when you hold "
        "no place there."
    ),
    parameters=tool_parameters(SetTurnStateArguments),
    effect=ToolEffect.SIDE_EFFECTING,
)


class LookArguments(BaseModel):
    """The arguments of `look`: the place whose person's camera the agent looks at."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    place: PlaceRef


_LOOK_DECLARATION: Final = ToolDeclaration(
    name=LOOK,
    description=(
        "Take one still picture of your person's camera in the call you joined, and see it. Nobody else's camera is "
        "ever shown to you. You see a picture only after your person has turned on the switch that lets you see them, "
        "which starts off in every call; your person's browser takes the picture when you ask, and nothing keeps it. "
        "The answer is the picture alone. It is refused as not_active when you are not joined there or your person's "
        "seat is away, as not_allowed when your person has not let you see them, as no_camera when their camera is "
        f"off, as too_fast above {LOOKS_PER_WINDOW} looks a minute, as no_answer when their browser does not answer in "
        "a few seconds and "
        "as unavailable when the picture could not be used. Your person can see on your tile each time you look."
    ),
    parameters=tool_parameters(LookArguments),
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


def _look_refused(refusal: LookRefusal) -> ToolOutcome:
    return ToolOutcome(json.dumps({"refused": refusal.value}), is_error=True)


def _gesture_refused() -> ToolOutcome:
    return ToolOutcome(GestureRefused(refused=GestureRefusal.NOT_ACTIVE).model_dump_json(), is_error=True)


def _framing_refused() -> ToolOutcome:
    return ToolOutcome(SetFramingRefused(refused=SetFramingRefusal.NOT_ACTIVE).model_dump_json(), is_error=True)


def _turn_state_refused() -> ToolOutcome:
    return ToolOutcome(SetTurnStateRefused(refused=SetTurnStateRefusal.NOT_HELD).model_dump_json(), is_error=True)


def _stop_refused(refusal: StopSpeakingRefusal) -> ToolOutcome:
    return ToolOutcome(StopSpeakingRefused(refused=refusal).model_dump_json(), is_error=True)


def _continue_refused(refusal: ContinueSpeakingRefusal) -> ToolOutcome:
    return ToolOutcome(ContinueSpeakingRefused(refused=refusal).model_dump_json(), is_error=True)


def _joined_or_refused(outcome: DialedAgent | AgentRefusal) -> ToolOutcome:
    if isinstance(outcome, DialedAgent):
        return ToolOutcome(json.dumps({"room": outcome.room_id, "name": outcome.name}), is_error=False)
    return refused(outcome)


def _text_of(arguments: JsonObject, name: str) -> str:
    value = arguments.get(name)
    return value if isinstance(value, str) else ""


def _figure_of(accepted: AcceptInvitationArguments) -> Figure | None:
    """The figure hopin shows: the first figure look of `looks`, which skips a video look as hopin draws none; the old
    `figure` only while `looks` is absent, and it carries no room."""
    if accepted.looks is None:
        return None if accepted.figure is None else _figure_with(accepted.figure, None)
    look = next((look for look in accepted.looks if isinstance(look, FigureLook)), None)
    return None if look is None else _figure_with(look, look.room)


def _figure_with(ref: FigureRef, room: HttpsFileRef | None) -> Figure:
    clips = tuple(Clip(gesture.value, clip.url, clip.sha256) for gesture, clip in ref.gestures.items())
    return Figure(ref.url, ref.sha256, clips, None if room is None else PersonaRoom(room.url, room.sha256))


@dataclass(frozen=True)
class AcceptedVideoLooks:
    """The call an accept joins and the names of the video looks it lists ahead of its figure look: the looks HopIn
    can show before it shows its figure, which it draws itself, in the agent's order."""

    room_id: str
    names: tuple[str, ...]


def video_looks_of_accept(arguments: JsonObject) -> AcceptedVideoLooks:
    accepted = member_arguments(AcceptInvitationArguments, arguments)
    names: list[str] = []
    for look in accepted.looks or ():
        if isinstance(look, FigureLook):
            break
        names.append(look.name)
    return AcceptedVideoLooks(accepted.place.id, tuple(names))


async def _accepted(participation: Participation, caller: Caller, arguments: JsonObject) -> ToolOutcome:
    accepted = member_arguments(AcceptInvitationArguments, arguments)
    if accepted.place.kind != CALL_KIND:
        return _accept_refused(AcceptInvitationRefusal.STALE_INVITATION)
    figure = _figure_of(accepted)
    voices = known_voices(accepted.voices)
    outcome = await participation.accept(
        caller.person, accepted.place.id, accepted.invitation, accepted.epoch, figure, voices
    )
    if isinstance(outcome, AgentRefusal):
        return _accept_refused(AcceptInvitationRefusal(outcome.value))
    if len(voices) < len(accepted.voices or ()):
        await logger.ainfo("voice_list.refused", reason="unknown_voice", room_ref=room_reference(accepted.place.id))
    return _joined_or_refused(outcome)


async def _declined(participation: Participation, caller: Caller, arguments: JsonObject) -> ToolOutcome:
    declined = member_arguments(DeclineInvitationArguments, arguments)
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
    request = member_arguments(SpeakArguments, arguments)
    if request.place.kind != CALL_KIND:
        return _speak_refused(SpeakRefusal.NOT_ACTIVE)
    if len(one_line(request.text)) > SPEAK_MAX_CHARACTERS:
        return _speak_refused(SpeakRefusal.TOO_LONG)
    render = (
        None if voice is None else partial(voice.render, caller.person, language=request.language, style=request.style)
    )
    try:
        outcome = await participation.speak(
            caller.person,
            request.place.id,
            request.text,
            render,
            None if voice is None else voice.offers,
            None if request.mood is None else FigureMood(request.mood.value),
            request.interrupted,
        )
    except LanguageUnavailableError:
        return _speak_refused(SpeakRefusal.LANGUAGE_UNAVAILABLE)
    except StyleUnavailableError:
        return _speak_refused(SpeakRefusal.STYLE_UNAVAILABLE)
    if isinstance(outcome, AgentRefusal | SpeakStop | HoldRefusal):
        return _speak_refused(_SPEAK_REFUSAL_OF[outcome])
    if outcome is SpeakFallback.WRITTEN:
        return ToolOutcome(Written(written=True).model_dump_json(), is_error=False)
    return ToolOutcome(Spoken(spoken=True).model_dump_json(), is_error=False)


async def _stopped(participation: Participation, caller: Caller, arguments: JsonObject) -> ToolOutcome:
    stopping = member_arguments(StopSpeakingArguments, arguments)
    if stopping.place.kind != CALL_KIND:
        return _stop_refused(StopSpeakingRefusal.NOT_ACTIVE)
    outcome = await participation.stop_speaking(caller.person, stopping.place.id, stopping.interrupted)
    if outcome is SpeakStop.DROPPED:
        return ToolOutcome(DroppedSpeaking(dropped=True).model_dump_json(), is_error=False)
    if isinstance(outcome, AgentRefusal | SpeakStop | HoldRefusal):
        return _stop_refused(_STOP_REFUSAL_OF[outcome])
    return ToolOutcome(StoppedSpeaking(cut=outcome).model_dump_json(), is_error=False)


async def _continued(participation: Participation, caller: Caller, arguments: JsonObject) -> ToolOutcome:
    continuing = member_arguments(ContinueSpeakingArguments, arguments)
    if continuing.place.kind != CALL_KIND:
        return _continue_refused(ContinueSpeakingRefusal.NOT_ACTIVE)
    refusal = await participation.continue_speaking(caller.person, continuing.place.id, continuing.interrupted)
    if refusal is not None:
        return _continue_refused(_CONTINUE_REFUSAL_OF[refusal])
    return ToolOutcome(ContinuedSpeaking(continued=True).model_dump_json(), is_error=False)


async def _gestured(participation: Participation, caller: Caller, arguments: JsonObject) -> ToolOutcome:
    gesturing = member_arguments(GestureArguments, arguments)
    if gesturing.place.kind != CALL_KIND:
        return _gesture_refused()
    gesture = FigureGesture(gesturing.gesture.value)
    if await participation.figures.gesture(caller.person, gesturing.place.id, gesture) is not None:
        return _gesture_refused()
    return ToolOutcome(Relayed(relayed=True).model_dump_json(), is_error=False)


async def _left(participation: Participation, caller: Caller, arguments: JsonObject) -> ToolOutcome:
    refusal = await participation.leave(caller.person, _text_of(arguments, "room"))
    if refusal is None:
        return ToolOutcome(json.dumps({"left": True}), is_error=False)
    return refused(refusal)


async def _said(participation: Participation, caller: Caller, arguments: JsonObject) -> ToolOutcome:
    refusal = await participation.say(caller.person, _text_of(arguments, "room"), _text_of(arguments, "text"))
    if refusal is None:
        return ToolOutcome(json.dumps({}), is_error=False)
    return refused(refusal)


async def _framed(participation: Participation, caller: Caller, arguments: JsonObject) -> ToolOutcome:
    framing = member_arguments(SetFramingArguments, arguments)
    if framing.place.kind != CALL_KIND:
        return _framing_refused()
    if await participation.figures.frame(caller.person, framing.place.id, FigureFraming(framing.framing.value)):
        return _framing_refused()
    return ToolOutcome(json.dumps({}), is_error=False)


async def _turn_stated(participation: Participation, caller: Caller, arguments: JsonObject) -> ToolOutcome:
    stating = member_arguments(SetTurnStateArguments, arguments)
    if stating.place.kind != CALL_KIND:
        return _turn_state_refused()
    stopped = None if stating.stopped is None else TurnOutcome(stating.stopped.value)
    refusal = await participation.figures.state_turn(
        caller.person, stating.place.id, thinking=stating.thinking, stopped=stopped
    )
    if refusal is not None:
        return _turn_state_refused()
    return ToolOutcome(json.dumps({}), is_error=False)


async def _looked(participation: Participation, caller: Caller, arguments: JsonObject) -> ToolOutcome:
    try:
        looking = LookArguments.model_validate(arguments)
    except ValidationError:
        return _look_refused(LookRefusal.NOT_ACTIVE)
    if looking.place.kind != CALL_KIND:
        return _look_refused(LookRefusal.NOT_ACTIVE)
    picture = await participation.look(caller.person, looking.place.id)
    if isinstance(picture, LookRefusal):
        return _look_refused(picture)
    return ToolOutcome("", is_error=False, image=ToolImage(content=picture))


def participation_tools(participation: Callable[[], Participation], voice: AgentVoice | None) -> tuple[CallerTool, ...]:
    """The tools on the participation the call's handler builds; `participation` is read at each call. `voice` speaks
    an agent's sentences, none while speaking is off."""

    async def join_call(caller: Caller, arguments: JsonObject) -> ToolOutcome:
        room, name = _text_of(arguments, "room"), _text_of(arguments, "name")
        return _joined_or_refused(await participation().dial_in(caller.person, room, name))

    async def accept_invitation(caller: Caller, arguments: JsonObject) -> ToolOutcome:
        return await _accepted(participation(), caller, arguments)

    async def decline_invitation(caller: Caller, arguments: JsonObject) -> ToolOutcome:
        return await _declined(participation(), caller, arguments)

    async def leave_call(caller: Caller, arguments: JsonObject) -> ToolOutcome:
        return await _left(participation(), caller, arguments)

    async def say(caller: Caller, arguments: JsonObject) -> ToolOutcome:
        return await _said(participation(), caller, arguments)

    async def speak(caller: Caller, arguments: JsonObject) -> ToolOutcome:
        return await _spoken(participation(), voice, caller, arguments)

    async def stop_speaking(caller: Caller, arguments: JsonObject) -> ToolOutcome:
        return await _stopped(participation(), caller, arguments)

    async def continue_speaking(caller: Caller, arguments: JsonObject) -> ToolOutcome:
        return await _continued(participation(), caller, arguments)

    async def gesture(caller: Caller, arguments: JsonObject) -> ToolOutcome:
        return await _gestured(participation(), caller, arguments)

    async def set_framing(caller: Caller, arguments: JsonObject) -> ToolOutcome:
        return await _framed(participation(), caller, arguments)

    async def set_turn_state(caller: Caller, arguments: JsonObject) -> ToolOutcome:
        return await _turn_stated(participation(), caller, arguments)

    async def look(caller: Caller, arguments: JsonObject) -> ToolOutcome:
        return await _looked(participation(), caller, arguments)

    return (
        CallerTool(_JOIN_CALL_DECLARATION, join_call),
        CallerTool(_ACCEPT_INVITATION_DECLARATION, accept_invitation),
        CallerTool(_DECLINE_INVITATION_DECLARATION, decline_invitation),
        CallerTool(_LEAVE_CALL_DECLARATION, leave_call),
        CallerTool(_SAY_DECLARATION, say),
        CallerTool(_SPEAK_DECLARATION, speak),
        CallerTool(_STOP_SPEAKING_DECLARATION, stop_speaking),
        CallerTool(_CONTINUE_SPEAKING_DECLARATION, continue_speaking),
        CallerTool(gesture_tool(), gesture),
        CallerTool(framing_tool(), set_framing),
        CallerTool(_SET_TURN_STATE_DECLARATION, set_turn_state),
        CallerTool(_LOOK_DECLARATION, look),
    )
