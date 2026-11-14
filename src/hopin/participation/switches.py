"""The switches of a joined agent's sponsor: whether the agent hears them and whether it sees them. Only the sponsor
presses one, and is answered with the server's record of it."""

from typing import TYPE_CHECKING

from hopin.errors import InvalidMessageError
from hopin.participation.frames import hearing_frame, refusal_frame, seeing_frame
from hopin.participation.vocabulary import AgentRefusal, LookRefusal, ParticipationSignal

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from hopin.participation.hearing import Hearing
    from hopin.participation.participants import DialedAgent
    from hopin.participation.places import Places
    from hopin.participation.seeing import Seeing
    from hopin.participation.speech_in_windows import SpeechInWindows
    from hopin.signaling.features import CallRooms
    from hopin.signaling.frames import ForeignFrame, JsonObject, PeerId


class Switches:
    """Applies the sponsor's switch frames. `tell` sends a frame to the seats of a room and `in_call` says whether the
    agent still holds its place."""

    def __init__(
        self,
        rooms: CallRooms,
        places: Places,
        hearing: Hearing,
        speech: SpeechInWindows,
        seeing: Seeing,
        tell: Callable[[str, Sequence[str], JsonObject], Awaitable[None]],
        in_call: Callable[[DialedAgent], bool],
    ) -> None:
        self._rooms = rooms
        self._places = places
        self._hearing = hearing
        self._speech = speech
        self._seeing = seeing
        self._tell = tell
        self._in_call = in_call

    async def on_frame(self, peer_id: PeerId, frame: ForeignFrame) -> None:
        """Apply a switch frame to the agent it names and answer the sponsor with the record; a seat other than the
        agent's sponsor is refused, and so is an agent that is not joined in the seat's room."""
        agent_id = frame.data.get("agent_id")
        on = frame.data.get("on")
        if not isinstance(agent_id, str) or not isinstance(on, bool):
            raise InvalidMessageError("A switch needs the agent's id and on or off")
        room_id = self._rooms.room_of(peer_id)
        agent = next(
            (agent for agent in self._places.joined() if agent.agent_id == agent_id and agent.room_id == room_id), None
        )
        if agent is None:
            await self._rooms.send(peer_id, refusal_frame(AgentRefusal.GONE))
        elif agent.sponsor != peer_id:
            await self._rooms.send(peer_id, refusal_frame(AgentRefusal.NOT_SPONSOR))
        elif frame.signal_type == ParticipationSignal.AGENT_HEARING:
            self._hearing.switch(agent, on=on)
            self._speech.hearing_switched(agent, on=on)
            await self.tell(agent)
        else:
            self._hearing.switch_seeing(agent, on=on)
            if not on:
                self._seeing.ended(agent, LookRefusal.NOT_ALLOWED)
            await self.tell(agent)

    async def tell(self, agent: DialedAgent) -> None:
        """Tell the agent's sponsor the server's record of the agent's switches, while the agent is in the call: the
        hearing one only while the call lets agents hear."""
        if not self._in_call(agent):
            return
        hearing = self._hearing.switch_of(agent)
        if hearing is not None:
            await self._tell(agent.room_id, [agent.sponsor], hearing_frame(agent, on=hearing))
        await self._tell(agent.room_id, [agent.sponsor], seeing_frame(agent, on=self._hearing.sees(agent)))
