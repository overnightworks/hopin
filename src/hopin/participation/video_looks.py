"""The video sessions agents hold while they are joined: one per agent at most, opened by the connector, kept until the
agent's place ends and closed with it, and the one `video_session.ended` line each attempt writes.

An attempt that ends for good, before a session opens or after it, leaves the agent on the next look it can show; the
connector opens no second session in that place. A session survives a parked sponsor, which keeps the agent's place.
Sound and picture of a session are not this module's: it knows a session only as something to close."""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from logline import StepTimer

from hopin.participation.participants import DialedAgent
from hopin.rooms.reference import room_reference

if TYPE_CHECKING:
    from hopin.participation.participants import Person
    from hopin.participation.places import Places
    from hopin.participation.vocabulary import VideoFailure

_STEPS: Final = logging.getLogger(__name__)


class VideoSession(Protocol):
    """An open session on the video service."""

    def close(self) -> None:
        """Begin closing the session: the connection ends on the connector's own task, so this never blocks."""


@dataclass(frozen=True)
class _Held:
    session: VideoSession
    attempt: StepTimer


class VideoLooks:
    """The video sessions of the agents joined now."""

    def __init__(self, places: Places) -> None:
        self._places = places
        self._held: dict[str, _Held] = {}

    def joined(self, person: Person, room_id: str) -> DialedAgent | None:
        """The person's agent joined in the room now, or nothing."""
        place = self._places.get(person, room_id)
        return place if isinstance(place, DialedAgent) else None

    @staticmethod
    def attempt(agent: DialedAgent) -> StepTimer:
        """The `video_session.ended` line of a new attempt, started now and written once, when the attempt ends."""
        return StepTimer("video_session.ended", _STEPS, room_ref=room_reference(agent.room_id), agent_id=agent.agent_id)

    def refused(self, agent: DialedAgent, attempt: StepTimer, failure: VideoFailure) -> None:
        """The attempt ends for good: its line says `refused` for `failure`, unless the agent's place ended first and
        wrote it as `ok`. The agent stays on the next look it can show."""
        if (held := self._held.get(agent.agent_id)) is not None and held.attempt is attempt:
            del self._held[agent.agent_id]
        attempt.refuse(failure.value)
        attempt.end()

    def opened(self, agent: DialedAgent, session: VideoSession, attempt: StepTimer) -> None:
        """Keep the session the attempt opened for the agent; a session handed over after the agent's place ended is
        closed at once."""
        if self.joined(agent.person, agent.room_id) is None:
            session.close()
            attempt.end()
            return
        self._held[agent.agent_id] = _Held(session, attempt)

    def agent_ended(self, agent: DialedAgent) -> None:
        """The agent's removal, leave, lease end, call end or HopIn's shutdown: its session closes and its line says
        `ok`."""
        held = self._held.pop(agent.agent_id, None)
        if held is not None:
            held.session.close()
            held.attempt.end()
