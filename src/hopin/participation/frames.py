"""The frames participation sends a seat, each a record the browser's wire is generated from."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, get_args

from hopin.participation.figures import clip_path, figure_path
from hopin.participation.vocabulary import (
    AgentRefusal,
    FigureGesture,
    FigureMood,
    InvitationOutcome,
    ParticipationSignal,
)
from hopin.signaling.frames import WireFrame, WireRecord

if TYPE_CHECKING:
    from hopin.participation.participants import DialedAgent


@dataclass(frozen=True)
class Offer(WireRecord):
    """An agent a seat may add: the agent's key, its name, and whether this seat's invitation of it is open."""

    agent_key: str
    name: str
    pending: bool


@dataclass(frozen=True)
class AgentJoinedFrame(WireFrame):
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_JOINED

    agent_id: str
    name: str
    sponsor: str
    sponsor_id: str
    figure: str | None = None
    gestures: dict[str, str] | None = None


@dataclass(frozen=True)
class AgentLeftFrame(WireFrame):
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_LEFT

    agent_id: str


@dataclass(frozen=True)
class AgentHearingFrame(WireFrame):
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_HEARING

    agent_id: str
    on: bool


@dataclass(frozen=True)
class AgentSeeingFrame(WireFrame):
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_SEEING

    agent_id: str
    on: bool


@dataclass(frozen=True)
class AgentLookRequestFrame(WireFrame):
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_LOOK_REQUEST

    request_id: str


@dataclass(frozen=True)
class AgentLookedFrame(WireFrame):
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_LOOKED

    agent_id: str


@dataclass(frozen=True)
class AgentVoiceFrame(WireFrame):
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_VOICE

    agent_id: str
    voice_id: str
    mood: FigureMood | None = None


@dataclass(frozen=True)
class AgentStopFrame(WireFrame):
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_STOP

    agent_id: str


@dataclass(frozen=True)
class AgentHeldFrame(WireFrame):
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_HELD

    agent_id: str


@dataclass(frozen=True)
class AgentContinuedFrame(WireFrame):
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_CONTINUED

    agent_id: str


@dataclass(frozen=True)
class AgentGestureFrame(WireFrame):
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_GESTURE

    agent_id: str
    gesture: FigureGesture


@dataclass(frozen=True)
class AgentThinkingFrame(WireFrame):
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_THINKING

    agent_id: str
    on: bool


@dataclass(frozen=True)
class AgentOffersFrame(WireFrame):
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_OFFERS

    shown: bool
    full: bool
    offers: list[Offer]


@dataclass(frozen=True)
class AgentInvitationEndedFrame(WireFrame):
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_INVITATION_ENDED

    agent_key: str
    name: str
    outcome: InvitationOutcome


@dataclass(frozen=True)
class AgentLineFrame(WireFrame):
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_LINE

    agent_id: str
    name: str
    text: str


@dataclass(frozen=True)
class AgentRefusedFrame(WireFrame):
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_REFUSED

    reason: AgentRefusal


type ParticipationFrame = (
    AgentJoinedFrame
    | AgentLeftFrame
    | AgentHearingFrame
    | AgentSeeingFrame
    | AgentLookRequestFrame
    | AgentLookedFrame
    | AgentVoiceFrame
    | AgentStopFrame
    | AgentHeldFrame
    | AgentContinuedFrame
    | AgentGestureFrame
    | AgentThinkingFrame
    | AgentOffersFrame
    | AgentInvitationEndedFrame
    | AgentLineFrame
    | AgentRefusedFrame
)

# The frames the generator renders into participation's own browser wire, in the order they appear there.
BROWSER_FRAMES = get_args(ParticipationFrame.__value__)


def joined_frame(agent: DialedAgent, sponsor_name: str) -> AgentJoinedFrame:
    figure = agent.figure
    return AgentJoinedFrame(
        agent_id=agent.agent_id,
        name=agent.name,
        sponsor=sponsor_name,
        sponsor_id=agent.sponsor,
        figure=None if figure is None else figure_path(figure.sha256),
        gestures={clip.gesture: clip_path(clip.sha256) for clip in figure.clips} if figure and figure.clips else None,
    )
