"""The frames participation sends a seat, each built from the agent it is about."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hopin.participation.figures import figure_path
from hopin.participation.vocabulary import AgentRefusal, ParticipationSignal
from hopin.signaling.frames import TYPE_KEY, JsonObject, JsonValue

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hopin.participation.feature import DialedAgent


def frame(signal: ParticipationSignal, **fields: JsonValue) -> JsonObject:
    return {TYPE_KEY: signal.value, **fields}


def joined_frame(agent: DialedAgent, sponsor_name: str) -> JsonObject:
    figure: dict[str, JsonValue] = {} if agent.figure is None else {"figure": figure_path(agent.figure.sha256)}
    return frame(
        ParticipationSignal.AGENT_JOINED,
        agent_id=agent.agent_id,
        name=agent.name,
        sponsor=sponsor_name,
        sponsor_id=agent.sponsor,
        **figure,
    )


def line_frame(agent: DialedAgent, text: str) -> JsonObject:
    return frame(ParticipationSignal.AGENT_LINE, agent_id=agent.agent_id, name=agent.name, text=text)


def hearing_frame(agent: DialedAgent, *, on: bool) -> JsonObject:
    return frame(ParticipationSignal.AGENT_HEARING, agent_id=agent.agent_id, on=on)


def voice_frame(agent: DialedAgent, voice_id: str) -> JsonObject:
    return frame(ParticipationSignal.AGENT_VOICE, agent_id=agent.agent_id, voice_id=voice_id)


def left_frame(agent: DialedAgent) -> JsonObject:
    return frame(ParticipationSignal.AGENT_LEFT, agent_id=agent.agent_id)


def refusal_frame(refusal: AgentRefusal) -> JsonObject:
    return frame(ParticipationSignal.AGENT_REFUSED, reason=refusal.value)


@dataclass(frozen=True)
class Offer:
    """An agent a seat may add: the agent's key, its name, and whether this seat's invitation of it is open."""

    agent_key: str
    name: str
    pending: bool


def offers_frame(offers: Sequence[Offer], *, shown: bool) -> JsonObject:
    return frame(
        ParticipationSignal.AGENT_OFFERS,
        shown=shown,
        offers=[{"agent_key": offer.agent_key, "name": offer.name, "pending": offer.pending} for offer in offers],
    )
