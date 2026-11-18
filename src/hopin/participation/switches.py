"""The switches of a joined agent's sponsor: whether the agent hears them and whether it sees them. Only the sponsor
presses one, and is answered with the server's record of it."""

from typing import TYPE_CHECKING

from hopin.errors import InvalidMessageError
from hopin.participation.frames import AgentHearingFrame, AgentRefusedFrame, AgentSeeingFrame
from hopin.participation.vocabulary import AgentRefusal, LookRefusal, ParticipationSignal

if TYPE_CHECKING:
    from hopin.participation.hearing import Hearing
    from hopin.participation.participants import DialedAgent
    from hopin.participation.places import Places
    from hopin.participation.seeing import Seeing
    from hopin.participation.speech_in_windows import SpeechInWindows
    from hopin.signaling.frames import ForeignFrame, PeerId
    from hopin.signaling.transition import RoomView, Transition


class Switches:
    """Applies the sponsor's switch frames, each in the transition of the sponsor's room."""

    def __init__(self, places: Places, hearing: Hearing, speech: SpeechInWindows, seeing: Seeing) -> None:
        self._places = places
        self._hearing = hearing
        self._speech = speech
        self._seeing = seeing

    def on_frame(self, peer_id: PeerId, frame: ForeignFrame, view: RoomView, transition: Transition) -> None:
        """Apply a switch frame to the agent it names and answer the sponsor with the record; a seat other than the
        agent's sponsor is refused, and so is an agent that is not joined in the seat's room."""
        agent_id = frame.data.get("agent_id")
        on = frame.data.get("on")
        if not isinstance(agent_id, str) or not isinstance(on, bool):
            raise InvalidMessageError("A switch needs the agent's id and on or off")
        room_id = view.room_of(peer_id)
        agent = next(
            (agent for agent in self._places.joined() if agent.agent_id == agent_id and agent.room_id == room_id), None
        )
        if agent is None:
            transition.send(peer_id, AgentRefusedFrame(AgentRefusal.GONE).to_json())
        elif agent.sponsor != peer_id:
            transition.send(peer_id, AgentRefusedFrame(AgentRefusal.NOT_SPONSOR).to_json())
        elif frame.signal_type == ParticipationSignal.AGENT_HEARING:
            self._hearing.switch(agent, on=on)
            self._speech.hearing_switched(agent, on=on)
            self.tell(view, agent, transition)
        else:
            self._hearing.switch_seeing(agent, on=on)
            if not on:
                self._seeing.ended(agent, LookRefusal.NOT_ALLOWED)
            self.tell(view, agent, transition)

    def tell(self, view: RoomView, agent: DialedAgent, transition: Transition) -> None:
        """Tell the agent's sponsor, who sits in the room, the server's record of the agent's switches: the hearing
        one only while the call lets agents hear."""
        del view
        hearing = self._hearing.switch_of(agent)
        if hearing is not None:
            transition.send(agent.sponsor, AgentHearingFrame(agent.agent_id, on=hearing).to_json())
        transition.send(agent.sponsor, AgentSeeingFrame(agent.agent_id, on=self._hearing.sees(agent)).to_json())
