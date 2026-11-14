"""The agents library's record of who may hear whom, as participation's port: a call is the place `call/<room>`."""

from typing import TYPE_CHECKING

from agents.mcp_server.hearing import MemberHearing, Utterance
from agents.mcp_server.hearing import Sense as AgentSense
from agents.member import PlaceRef
from agents.tools import Principal
from pydantic import ValidationError

from hopin.agent.tools import CALL_KIND
from hopin.participation.chat_window import LineKind
from hopin.participation.hearing import HeardLine
from hopin.participation.participants import Person

if TYPE_CHECKING:
    from hopin.participation.vocabulary import Sense
    from hopin.signaling.frames import PeerId


def _place_of(room_id: str) -> PlaceRef:
    return PlaceRef(kind=CALL_KIND, id=room_id)


def _principal_of(person: Person) -> Principal:
    return Principal(iss=person.issuer, sub=person.subject)


def _person_of(principal: Principal) -> Person:
    return Person(principal.iss, principal.sub)


class MemberHearingRecord:
    """Adapts `MemberHearing` to participation's `HearingRecord`: the seat is the speaker's key."""

    def __init__(self, record: MemberHearing) -> None:
        self._record = record

    def allow(self, room_id: str, seat: PeerId, agent: Person, sense: Sense, *, allowed: bool) -> None:
        self._record.allow(_place_of(room_id), seat, _principal_of(agent), AgentSense(sense.value), allowed)

    def sensing(self, room_id: str, seat: PeerId, sense: Sense) -> frozenset[Person]:
        return frozenset(map(_person_of, self._record.sensing(_place_of(room_id), seat, AgentSense(sense.value))))

    def heard(self, room_id: str, seat: PeerId, name: str, text: str, began_at: float) -> HeardLine | None:
        place = _place_of(room_id)
        try:
            utterance = Utterance(
                place=place, speaker=seat, kind=LineKind.PERSON.value, name=name, text=text, began_at=began_at
            )
        except ValidationError as refusal:
            # Only text that may not be heard is refused quietly; any other field is HopIn's own and fails loudly.
            if {error["loc"] for error in refusal.errors()} == {("text",)}:
                return None
            raise
        heard = self._record.heard(utterance)
        line = heard.line
        return HeardLine(frozenset(map(_person_of, heard.members)), LineKind(line.kind), line.name, line.text)

    def member_ended(self, room_id: str, agent: Person) -> None:
        self._record.member_ended(_place_of(room_id), _principal_of(agent))

    def speaker_ended(self, room_id: str, seat: PeerId) -> None:
        self._record.speaker_ended(_place_of(room_id), seat)

    def place_ended(self, room_id: str) -> None:
        self._record.place_ended(_place_of(room_id))
