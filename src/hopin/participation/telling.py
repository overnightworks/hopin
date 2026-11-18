"""How a transition of participation tells the seats of a room of its agents: their tiles coming and going, and the
frames only the seats still in the room get."""

from typing import TYPE_CHECKING

import structlog

from hopin.participation.frames import AgentLeftFrame, joined_frame
from hopin.participation.vocabulary import ParticipationSignal
from hopin.rooms.reference import room_reference
from hopin.signaling.replay import Supersedes

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hopin.participation.frames import ParticipationFrame
    from hopin.participation.participants import DialedAgent
    from hopin.signaling.frames import PeerId
    from hopin.signaling.transition import RoomView, Transition

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


def send_in_room(
    view: RoomView,
    transition: Transition,
    peer_ids: Sequence[PeerId],
    frame: ParticipationFrame,
    supersedes: Supersedes | None = None,
) -> None:
    """Send to each seat that still sits in the room."""
    for peer_id in peer_ids:
        if view.room_of(peer_id) is not None:
            transition.send(peer_id, frame.to_json(), supersedes)


def tell_joined(view: RoomView, agent: DialedAgent, peer_ids: Sequence[PeerId], transition: Transition) -> None:
    """Tell the seats of the agent's tile."""
    frame = joined_frame(agent, view.name_of(agent.sponsor))
    for peer_id in peer_ids:
        transition.send(peer_id, frame.to_json())


def tell_left(agent: DialedAgent, recipients: Sequence[PeerId], transition: Transition) -> None:
    """Tell the seats that the agent's tile is gone."""
    # A tile or a voice not yet written to a seat never reaches it once its agent has left.
    unannounced = Supersedes(
        frozenset({ParticipationSignal.AGENT_JOINED, ParticipationSignal.AGENT_VOICE}),
        holding=("agent_id", agent.agent_id),
    )
    for peer_id in recipients:
        transition.send(peer_id, AgentLeftFrame(agent.agent_id).to_json(), unannounced)
    logger.info("agent.left", room_ref=room_reference(agent.room_id), agent_id=agent.agent_id)


def voices_of(agent: DialedAgent) -> Supersedes:
    """The agent's voices not yet written to a seat, which a later voice, a stop or a leave of the agent makes moot."""
    return Supersedes(frozenset({ParticipationSignal.AGENT_VOICE}), holding=("agent_id", agent.agent_id))
