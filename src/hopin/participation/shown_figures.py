"""The figures the joined agents show on their tiles: which a digest names, whether an agent shows one, and the
gestures it makes with it."""

from typing import TYPE_CHECKING

from hopin.participation.figures import NamedFigure
from hopin.participation.frames import gesture_frame
from hopin.participation.participants import DialedAgent
from hopin.participation.vocabulary import AgentRefusal

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from hopin.participation.participants import Person
    from hopin.participation.places import Places
    from hopin.signaling.frames import JsonObject, PeerId


class ShownFigures:
    """`active_agent` names a person's joined agent in a room or why it is not; `tell` sends a frame to seats and
    `recipients` names the seats of a room."""

    def __init__(
        self,
        places: Places,
        active_agent: Callable[[Person, str], DialedAgent | AgentRefusal],
        tell: Callable[[str, Sequence[PeerId], JsonObject], Awaitable[None]],
        recipients: Callable[[str], list[PeerId]],
    ) -> None:
        self._places = places
        self._active_agent = active_agent
        self._tell = tell
        self._recipients = recipients

    def named(self, sha256: str) -> tuple[NamedFigure, ...]:
        """The URLs the joined agents name for the digest."""
        return tuple(
            NamedFigure(agent.room_id, agent.figure.url)
            for agent in self._places.joined()
            if agent.figure is not None and agent.figure.sha256 == sha256
        )

    def shown_by(self, person: Person) -> bool:
        """Whether the person's agent shows a figure in some call, which the gesture tool is offered for."""
        return any(
            isinstance(place, DialedAgent) and place.figure is not None for _, place in self._places.of_person(person)
        )

    async def gesture(self, person: Person, room_id: str, gesture: str) -> AgentRefusal | None:
        """Tell every seat the person's agent makes the gesture with its figure; why not when the agent holds no active
        place there or shows no figure. A seat that joins later is told no past gesture."""
        agent = self._active_agent(person, room_id)
        if isinstance(agent, AgentRefusal):
            return agent
        if agent.figure is None:
            return AgentRefusal.GONE
        await self._tell(room_id, self._recipients(room_id), gesture_frame(agent, gesture))
        return None
