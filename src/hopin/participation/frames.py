"""The frames participation sends a seat, each a record the browser's wire is generated from."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, get_args

from hopin.participation.figures import clip_path, figure_path
from hopin.participation.vocabulary import AgentRefusal, FigureGesture, InvitationOutcome, ParticipationSignal
from hopin.signaling.frames import TYPE_KEY, JsonObject, JsonValue, with_present

if TYPE_CHECKING:
    from hopin.participation.participants import DialedAgent


@dataclass(frozen=True)
class Offer:
    """An agent a seat may add: the agent's key, its name, and whether this seat's invitation of it is open."""

    agent_key: str
    name: str
    pending: bool

    def to_json(self) -> JsonObject:
        return {"agent_key": self.agent_key, "name": self.name, "pending": self.pending}


@dataclass(frozen=True)
class AgentJoinedFrame:
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_JOINED

    agent_id: str
    name: str
    sponsor: str
    sponsor_id: str
    figure: str | None = None
    gestures: dict[str, str] | None = None

    def to_json(self) -> JsonObject:
        frame: JsonObject = {
            TYPE_KEY: self.TYPE.value,
            "agent_id": self.agent_id,
            "name": self.name,
            "sponsor": self.sponsor,
            "sponsor_id": self.sponsor_id,
        }
        # A copy: the frame goes into the seat's replay log, and dict[str, str] is not a JSON value.
        gestures: JsonValue = None if self.gestures is None else {**self.gestures}
        return with_present(frame, figure=self.figure, gestures=gestures)


@dataclass(frozen=True)
class AgentLeftFrame:
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_LEFT

    agent_id: str

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "agent_id": self.agent_id}


@dataclass(frozen=True)
class AgentHearingFrame:
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_HEARING

    agent_id: str
    on: bool

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "agent_id": self.agent_id, "on": self.on}


@dataclass(frozen=True)
class AgentSeeingFrame:
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_SEEING

    agent_id: str
    on: bool

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "agent_id": self.agent_id, "on": self.on}


@dataclass(frozen=True)
class AgentLookRequestFrame:
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_LOOK_REQUEST

    request_id: str

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "request_id": self.request_id}


@dataclass(frozen=True)
class AgentLookedFrame:
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_LOOKED

    agent_id: str

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "agent_id": self.agent_id}


@dataclass(frozen=True)
class AgentVoiceFrame:
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_VOICE

    agent_id: str
    voice_id: str

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "agent_id": self.agent_id, "voice_id": self.voice_id}


@dataclass(frozen=True)
class AgentStopFrame:
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_STOP

    agent_id: str

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "agent_id": self.agent_id}


@dataclass(frozen=True)
class AgentGestureFrame:
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_GESTURE

    agent_id: str
    gesture: FigureGesture

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "agent_id": self.agent_id, "gesture": self.gesture.value}


@dataclass(frozen=True)
class AgentThinkingFrame:
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_THINKING

    agent_id: str
    on: bool

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "agent_id": self.agent_id, "on": self.on}


@dataclass(frozen=True)
class AgentOffersFrame:
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_OFFERS

    shown: bool
    full: bool
    offers: list[Offer]

    def to_json(self) -> JsonObject:
        return {
            TYPE_KEY: self.TYPE.value,
            "shown": self.shown,
            "full": self.full,
            "offers": [offer.to_json() for offer in self.offers],
        }


@dataclass(frozen=True)
class AgentInvitationEndedFrame:
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_INVITATION_ENDED

    agent_key: str
    outcome: InvitationOutcome

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "agent_key": self.agent_key, "outcome": self.outcome.value}


@dataclass(frozen=True)
class AgentLineFrame:
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_LINE

    agent_id: str
    name: str
    text: str

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "agent_id": self.agent_id, "name": self.name, "text": self.text}


@dataclass(frozen=True)
class AgentRefusedFrame:
    TYPE: ClassVar[ParticipationSignal] = ParticipationSignal.AGENT_REFUSED

    reason: AgentRefusal

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "reason": self.reason.value}


type ParticipationFrame = (
    AgentJoinedFrame
    | AgentLeftFrame
    | AgentHearingFrame
    | AgentSeeingFrame
    | AgentLookRequestFrame
    | AgentLookedFrame
    | AgentVoiceFrame
    | AgentStopFrame
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
