"""What a joined agent sees of its sponsor: the looks its sponsor's seeing switch allows.

A look needs the switch on and stays within the looks a window allows. It is asked of the sponsor's browser, which
captures its own camera, and a picture it yields is shown to the call as a mark on the agent's tile."""

from functools import partial
from typing import TYPE_CHECKING

from hopin.participation.frames import look_request_frame, looked_frame
from hopin.participation.looking import Looking
from hopin.participation.vocabulary import LookRefusal

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from hopin.participation.hearing import Hearing
    from hopin.participation.looking import Picture
    from hopin.participation.participants import DialedAgent
    from hopin.participation.places import Places
    from hopin.signaling.frames import ForeignFrame, JsonObject, PeerId
    from hopin.signaling.scheduler import Scheduler


class Seeing:
    """The rule of looks over the seeing switch `Hearing` records, which starts off for every agent. `tell` sends a
    frame to the seats of a room and `recipients` names a room's seats."""

    def __init__(
        self,
        hearing: Hearing,
        places: Places,
        scheduler: Scheduler,
        tell: Callable[[str, Sequence[str], JsonObject], Awaitable[None]],
        recipients: Callable[[str], list[PeerId]],
    ) -> None:
        self._hearing = hearing
        self._places = places
        self._scheduler = scheduler
        self._tell = tell
        self._recipients = recipients
        self._looking = Looking(scheduler, self._ask)

    async def look(self, agent: DialedAgent) -> Picture:
        """The picture of the sponsor's camera, or why there is none: the switch is off, the agent looked too often,
        the browser gave none or the agent or the switch ended meanwhile, which stops the look at whatever step it is
        on. Every seat of the call is shown the look when it yields a picture."""
        if not self._hearing.sees(agent):
            return LookRefusal.NOT_ALLOWED
        if not self._places.take_look(agent.person, agent.room_id, self._scheduler.now()):
            return LookRefusal.TOO_FAST
        return await self._looking.look(agent, partial(self._show, agent))

    def answered(self, sender: PeerId, frame: ForeignFrame, *, part: bool) -> None:
        """Take the sponsor's browser's answer to a look."""
        self._looking.answered(sender, frame, part=part)

    def ended(self, agent: DialedAgent, refusal: LookRefusal = LookRefusal.NOT_ACTIVE) -> None:
        """The agent's removal, leave, pause, lease end or call end, or the sponsor's turning its seeing off: its
        waiting looks yield nothing."""
        self._looking.end(agent, refusal)

    async def _ask(self, agent: DialedAgent, request_id: str) -> None:
        await self._tell(agent.room_id, [agent.sponsor], look_request_frame(request_id))

    async def _show(self, agent: DialedAgent) -> None:
        await self._tell(agent.room_id, self._recipients(agent.room_id), looked_frame(agent))
