"""The agents library's record of who may hear whom, as participation's port: a call is the place `call/<room>`."""

from typing import TYPE_CHECKING

from agents.mcp_server.hearing import MemberHearing, Sense, Utterance
from agents.member import PlaceRef
from agents.tools import Principal

from hopin.agent.tools import CALL_KIND
from hopin.participation.chat_window import LineKind
from hopin.participation.feature import Person

if TYPE_CHECKING:
    from hopin.signaling.frames import PeerId


def _place_of(room_id: str) -> PlaceRef:
    return PlaceRef(kind=CALL_KIND, id=room_id)


def _principal_of(person: Person) -> Principal:
    return Principal(iss=person.issuer, sub=person.subject)


def _person_of(principal: Principal) -> Person:
    return Person(principal.iss, principal.sub)


class MemberHearingRecord:
    """Adapts `MemberHearing` to participation's `HearingRecord`: the seat is the speaker's key, hearing the sense."""

    def __init__(self, record: MemberHearing) -> None:
        self._record = record

    def allow(self, room_id: str, seat: PeerId, agent: Person, *, allowed: bool) -> None:
        self._record.allow(_place_of(room_id), seat, _principal_of(agent), Sense.HEARING, allowed)

    def sensing(self, room_id: str, seat: PeerId) -> frozenset[Person]:
        return frozenset(map(_person_of, self._record.sensing(_place_of(room_id), seat, Sense.HEARING)))

    def heard(self, room_id: str, seat: PeerId, name: str, text: str, began_at: float) -> frozenset[Person]:
        utterance = Utterance(
            place=_place_of(room_id), speaker=seat, kind=LineKind.PERSON.value, name=name, text=text, began_at=began_at
        )
        return frozenset(map(_person_of, self._record.heard(utterance).members))

    def member_ended(self, room_id: str, agent: Person) -> None:
        self._record.member_ended(_place_of(room_id), _principal_of(agent))

    def speaker_ended(self, room_id: str, seat: PeerId) -> None:
        self._record.speaker_ended(_place_of(room_id), seat)

    def place_ended(self, room_id: str) -> None:
        self._record.place_ended(_place_of(room_id))
