"""The figures the joined agents show on their tiles: which a digest names, whether an agent shows one, the
gestures it makes with it and whether it thinks."""

from typing import TYPE_CHECKING

from hopin.participation.figures import NamedFigure
from hopin.participation.frames import AgentGestureFrame, AgentThinkingFrame, ParticipationFrame
from hopin.participation.participants import DialedAgent
from hopin.participation.vocabulary import THINKING_MAX_SECONDS, AgentRefusal, FigureGesture
from hopin.signaling.scheduler import DeadlineKind

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from hopin.participation.participants import Person
    from hopin.participation.places import Places
    from hopin.signaling.frames import PeerId
    from hopin.signaling.scheduler import Deadlines


class ShownFigures:
    """`active_agent` names a person's joined agent in a room or why it is not; `tell` sends a frame to seats and
    `recipients` names the seats of a room. An agent thinks from its statement until its next speak, gesture or
    statement at its place, or `THINKING_MAX_SECONDS`; nothing else starts it."""

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
        await self.end_thinking(agent)
        if agent.figure is None:
            return AgentRefusal.GONE
        await self._tell(room_id, self._recipients(room_id), AgentGestureFrame(agent.agent_id, gesture))
        return None

    async def state_turn(self, person: Person, room_id: str, *, thinking: bool) -> AgentRefusal | None:
        """Take the turn state the person's agent states at its place, active or paused: its stated thinking, or an
        end of it; why not when it holds no place there. A repeated thinking runs its deadline afresh."""
        agent = self._places.get(person, room_id)
        if not isinstance(agent, DialedAgent):
            return AgentRefusal.GONE
        if not thinking:
            await self.end_thinking(agent)
            return None
        self._deadlines.set(DeadlineKind.THINKING, agent.agent_id, THINKING_MAX_SECONDS)
        if agent.agent_id in self._thinking:
            return None
        self._thinking[agent.agent_id] = agent
        await self._tell(room_id, self._recipients(room_id), AgentThinkingFrame(agent.agent_id, on=True))
        return None

    async def end_thinking(self, agent: DialedAgent) -> None:
        """The agent spoke or gestured at its place: tell every seat it no longer thinks, when it did."""
        if self._thinking.pop(agent.agent_id, None) is None:
            return
        self._deadlines.cancel(DeadlineKind.THINKING, agent.agent_id)
        await self._tell(agent.room_id, self._recipients(agent.room_id), AgentThinkingFrame(agent.agent_id, on=False))

    async def tell_thinking(self, agent: DialedAgent, peer_id: PeerId) -> None:
        """Tell a seat that joined later that the agent thinks, when it does."""
        if agent.agent_id in self._thinking:
            await self._tell(agent.room_id, [peer_id], AgentThinkingFrame(agent.agent_id, on=True))

    async def _thinking_ran_out(self, agent_id: str) -> None:
        agent = self._thinking.pop(agent_id, None)
        if agent is not None and self._places.get(agent.person, agent.room_id) == agent:
            await self._tell(agent.room_id, self._recipients(agent.room_id), AgentThinkingFrame(agent_id, on=False))
