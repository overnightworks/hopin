"""What the seats report of a held sentence: how far their player stood in it, kept in memory by the hold's id until the
hold ends. A seat is trusted for nothing beyond its session, so a report is checked as input, bounded above by the
server's own clock for the sentence, and only logged."""

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from hopin.errors import InvalidMessageError

if TYPE_CHECKING:
    from collections.abc import Callable

    from hopin.participation.participants import DialedAgent
    from hopin.participation.places import Places
    from hopin.participation.speaking import HeldSentence
    from hopin.signaling.frames import ForeignFrame, PeerId
    from hopin.signaling.transition import RoomView

MILLISECONDS_PER_SECOND = 1000


@dataclass
class _Reports:
    """The played seconds each seat reported for one hold, by the seat's peer id, and how many were cut down to the
    server's clock."""

    played: dict[PeerId, float] = field(default_factory=dict)
    cut: int = 0


class HoldReports:
    """The reports of the seats for the holds that stand, by the hold's id. `held_sentence` names the sentence an
    agent's standing hold keeps."""

    def __init__(self, places: Places, held_sentence: Callable[[DialedAgent], HeldSentence | None]) -> None:
        self._places = places
        self._held_sentence = held_sentence
        self._reports: dict[str, _Reports] = {}

    def reported(self, peer_id: PeerId, frame: ForeignFrame, view: RoomView) -> None:
        """Keep the seconds the seat's player stood in the sentence an agent's hold keeps; a played time that is no
        finite non-negative number is an invalid message. A report is dropped without an answer when the agent is not
        joined in the seat's room, no hold of it stands, the voice is not the held sentence's, the sentence never
        started or was cut, or the seat already reported for the hold. More than the server's clock says the
        sentence played is cut down to it."""
        agent_id = frame.data.get("agent_id")
        voice_id = frame.data.get("voice_id")
        played = frame.data.get("played_seconds")
        if (
            not isinstance(agent_id, str)
            or not isinstance(voice_id, str)
            or isinstance(played, bool)
            or not isinstance(played, int | float)
            or not math.isfinite(played)
            or played < 0
        ):
            raise InvalidMessageError(
                "A hold report needs the agent's id, the voice's id and the finite played seconds"
            )
        room_id = view.room_of(peer_id)
        agent = next((place for place in self._places.joined() if place.agent_id == agent_id), None)
        if agent is None or agent.room_id != room_id:
            return
        held = self._held_sentence(agent)
        if held is None or held.voice_id != voice_id:
            return
        reports = self._reports.setdefault(held.hold_id, _Reports())
        if peer_id in reports.played:
            return
        reports.played[peer_id] = min(played, held.played_seconds)
        reports.cut += played > held.played_seconds

    def counts(self, hold_id: str) -> dict[str, int]:
        """The fields a hold's line carries of its reports: how many seats reported, the smallest played time in
        milliseconds when any did, and how many reports were cut."""
        reports = self._reports.get(hold_id, _Reports())
        fields = {"seat_report_count": len(reports.played), "seat_report_cut_count": reports.cut}
        if reports.played:
            fields["seat_played_min_ms"] = round(min(reports.played.values()) * MILLISECONDS_PER_SECOND)
        return fields

    def clear(self, hold_id: str) -> None:
        """Forget the reports of a hold that ended."""
        self._reports.pop(hold_id, None)
