"""The figures the joined agents show on their tiles: which a digest names, whether an agent shows one, the
gestures it makes with it, whether it thinks and why its turns do not answer."""

from typing import TYPE_CHECKING

import structlog

from hopin.errors import InvalidMessageError
from hopin.participation.figures import NamedFigure
from hopin.participation.frames import AgentGestureFrame, AgentThinkingFrame, AgentTurnStateFrame, ParticipationFrame
from hopin.participation.participants import DialedAgent
from hopin.participation.vocabulary import THINKING_MAX_SECONDS, AgentRefusal, FigureEnd, FigureGesture, TurnOutcome
from hopin.rooms.reference import room_reference
from hopin.signaling.scheduler import DeadlineKind

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from hopin.participation.participants import Person
    from hopin.participation.places import Places
    from hopin.signaling.frames import ForeignFrame, PeerId
    from hopin.signaling.scheduler import Deadlines
    from hopin.signaling.transition import RoomView, Transition

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class ShownFigures:
    """`active_agent` names a person's joined agent in a room or why it is not; `tell` sends a frame to seats and
    `recipients` names the seats of a room. An agent thinks from its statement until its next speak, gesture or
    statement at its place, or `THINKING_MAX_SECONDS`; nothing else starts it. The outcome an agent states for its
    stopped turns stands until its next turn state, speak, gesture or line, or its end."""

    def __init__(
        self,
        places: Places,
        deadlines: Deadlines,
        active_agent: Callable[[Person, str], DialedAgent | AgentRefusal],
        tell: Callable[[str, Sequence[PeerId], ParticipationFrame], Awaitable[None]],
        recipients: Callable[[str], list[PeerId]],
    ) -> None:
        self._places = places
        self._deadlines = deadlines
        self._active_agent = active_agent
        self._tell = tell
        self._recipients = recipients
        # The agents that think, by their id; an agent that ended meanwhile stays here until its deadline.
        self._thinking: dict[str, DialedAgent] = {}
        # The outcome each agent last stated for its stopped turns, by the agent's id.
        self._stopped: dict[str, TurnOutcome] = {}
        # The seats that reported an agent's figure ended, by the agent's id, for as long as the agent is joined.
        self._ended: dict[str, set[PeerId]] = {}
        deadlines.register(DeadlineKind.THINKING, self._thinking_ran_out)

    def named(self, sha256: str) -> tuple[NamedFigure, ...]:
        """The URLs the joined agents name for the digest."""
        return tuple(
            NamedFigure(agent.room_id, agent.figure.url)
            for agent in self._places.joined()
            if agent.figure is not None and agent.figure.sha256 == sha256
        )

    def named_clips(self, sha256: str) -> tuple[NamedFigure, ...]:
        """The URLs the joined agents name for the digest of a clip."""
        return tuple(
            NamedFigure(agent.room_id, clip.url)
            for agent in self._places.joined()
            if agent.figure is not None
            for clip in agent.figure.clips
            if clip.sha256 == sha256
        )

    def shown_by(self, person: Person) -> bool:
        """Whether the person's agent shows a figure in some call, which the gesture tool is offered for."""
        return any(
            isinstance(place, DialedAgent) and place.figure is not None for _, place in self._places.of_person(person)
        )

    async def gesture(self, person: Person, room_id: str, gesture: FigureGesture) -> AgentRefusal | None:
        """Tell every seat the person's agent makes the gesture with its figure; why not when the agent holds no active
        place there or shows no figure. A seat that joins later is told no past gesture."""
        agent = self._active_agent(person, room_id)
        if isinstance(agent, AgentRefusal):
            return agent
        await self.end_turn_state(agent)
        if agent.figure is None:
            return AgentRefusal.GONE
        await self._tell(room_id, self._recipients(room_id), AgentGestureFrame(agent.agent_id, gesture))
        return None

    async def state_turn(
        self, person: Person, room_id: str, *, thinking: bool, stopped: TurnOutcome | None = None
    ) -> AgentRefusal | None:
        """Take the turn state the person's agent states at its place, active or paused: its stated thinking, why its
        turns stop, or an end of both; why not when it holds no place there. A repeated thinking runs its deadline
        afresh."""
        agent = self._places.get(person, room_id)
        if not isinstance(agent, DialedAgent):
            return AgentRefusal.GONE
        await self._set_stopped(agent, stopped)
        if not thinking:
            await self.end_thinking(agent)
            return None
        self._deadlines.set(DeadlineKind.THINKING, agent.agent_id, THINKING_MAX_SECONDS)
        if agent.agent_id in self._thinking:
            return None
        self._thinking[agent.agent_id] = agent
        await self._tell(room_id, self._recipients(room_id), AgentThinkingFrame(agent.agent_id, on=True))
        return None

    async def end_turn_state(self, agent: DialedAgent) -> None:
        """The agent spoke, said a line or gestured at its place: tell every seat it no longer thinks and states no
        outcome, when it did."""
        await self.end_thinking(agent)
        await self._set_stopped(agent, None)

    def agent_ended(self, agent: DialedAgent) -> None:
        """Forget the outcome of an agent that left; its seats drop it with the agent's tile."""
        self._stopped.pop(agent.agent_id, None)

    async def end_thinking(self, agent: DialedAgent) -> None:
        """Tell every seat the agent no longer thinks, when it did."""
        if self._thinking.pop(agent.agent_id, None) is None:
            return
        self._deadlines.cancel(DeadlineKind.THINKING, agent.agent_id)
        await self._tell(agent.room_id, self._recipients(agent.room_id), AgentThinkingFrame(agent.agent_id, on=False))

    def tell_turn_state(self, agent: DialedAgent, peer_id: PeerId, transition: Transition) -> None:
        """Tell a seat that joined later, in the transition that announces the agent to it, that the agent thinks and
        the outcome it stated, when it did."""
        if agent.agent_id in self._thinking:
            transition.send(peer_id, AgentThinkingFrame(agent.agent_id, on=True).to_json())
        if (outcome := self._stopped.get(agent.agent_id)) is not None:
            transition.send(peer_id, AgentTurnStateFrame(agent.agent_id, outcome).to_json())

    def ended(self, peer_id: PeerId, frame: ForeignFrame, view: RoomView) -> None:
        """Write down the closed code a seat reports for why an agent's figure ended, with ids and the code only; a
        code outside the set is an invalid message, and a report for an agent not joined in the seat's room, or a
        repeat of a seat for an agent, is dropped without an answer."""
        agent_id = frame.data.get("agent_id")
        code = frame.data.get("code")
        if not isinstance(agent_id, str) or not isinstance(code, str) or code not in FigureEnd:
            raise InvalidMessageError("A figure end needs the agent's id and one of the closed codes")
        room_id = view.room_of(peer_id)
        joined = {agent.agent_id: agent.room_id for agent in self._places.joined()}
        self._ended = {agent: peers for agent, peers in self._ended.items() if agent in joined}
        if room_id is None or joined.get(agent_id) != room_id or peer_id in self._ended.get(agent_id, ()):
            return
        self._ended.setdefault(agent_id, set()).add(peer_id)
        logger.info("agent_figure.ended", room_ref=room_reference(room_id), agent_id=agent_id, code=code)

    def seat_left(self, peer_id: PeerId) -> None:
        """Forget the figure ends the seat reported, so a seat that comes back does not grow what is kept."""
        self._ended = {agent: peers - {peer_id} for agent, peers in self._ended.items() if peers - {peer_id}}

    async def _set_stopped(self, agent: DialedAgent, outcome: TurnOutcome | None) -> None:
        """Keep the outcome the agent states, or none, and tell every seat when it differs from the kept one."""
        if self._stopped.get(agent.agent_id) == outcome:
            return
        if outcome is None:
            del self._stopped[agent.agent_id]
        else:
            self._stopped[agent.agent_id] = outcome
        await self._tell(agent.room_id, self._recipients(agent.room_id), AgentTurnStateFrame(agent.agent_id, outcome))

    async def _thinking_ran_out(self, agent_id: str) -> None:
        agent = self._thinking.pop(agent_id, None)
        if agent is not None and self._places.get(agent.person, agent.room_id) == agent:
            await self._tell(agent.room_id, self._recipients(agent.room_id), AgentThinkingFrame(agent_id, on=False))
