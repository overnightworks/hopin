"""The records of a call and the rules that decide on them; a record is immutable, and a rule returns the one it makes."""

import hashlib
import secrets
from dataclasses import dataclass, field, replace

from hopin.errors import NotHostError, RoomFullError, RoomLockedError


@dataclass(frozen=True)
class Account:
    id: str
    name: str


@dataclass(frozen=True)
class Participant:
    participant_id: str
    account: Account


@dataclass(frozen=True)
class HostGrace:
    """The wait for a host who left: which of the host's seats left, when the room ends if no host is back and the
    process generation that times it."""

    host_peer_id: str
    deadline: float
    generation: str


@dataclass(frozen=True)
class AgentPlace:
    """A place a joined agent takes among the people, and the process generation that took it."""

    agent_id: str
    generation: str


@dataclass(frozen=True)
class Departure:
    """What the rest of a room must hear after a seat left it, and the process generation that tells them."""

    room_id: str
    peer_id: str
    name: str
    remaining: tuple[str, ...]
    host_left: bool
    generation: str


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


@dataclass(frozen=True)
class Park:
    dropped_at: float


@dataclass(frozen=True)
class SeatRecord:
    """The stored part of a seat: the account behind it, the hash of its resume token, its room, its park, the process
    generation that owns it, what the browser has sent (`received_seq`) and the seq its log reaches back to (`floor`).
    The seat's frames are in its log, never in the record. `version` is the record's compare-and-set counter."""

    account: Account
    token_hash: bytes = field(repr=False)
    generation: str
    room_id: str | None = None
    park: Park | None = None
    received_seq: int = 0
    floor: int = 0
    version: int = 0

    def accepts(self, token: str) -> bool:
        """Whether the token is the seat's, compared by its hash in constant time."""
        try:
            claimed = hash_token(token)
        except UnicodeEncodeError:
            return False
        return secrets.compare_digest(claimed, self.token_hash)

    def reaches_back_to(self, last_seq: int) -> bool:
        return last_seq >= self.floor


@dataclass(frozen=True)
class Room:
    room_id: str
    host: Account
    locked: bool = False
    grace: HostGrace | None = None
    participants: tuple[Participant, ...] = ()
    places: tuple[AgentPlace, ...] = ()
    departures: tuple[Departure, ...] = ()
    version: int = 0

    @property
    def participant_count(self) -> int:
        return len(self.participants)

    @property
    def participant_ids(self) -> list[str]:
        return [participant.participant_id for participant in self.participants]

    @property
    def agent_ids(self) -> frozenset[str]:
        return frozenset(place.agent_id for place in self.places)

    @property
    def host_present(self) -> bool:
        return self.host_present_without(None)

    def host_present_without(self, participant_id: str | None) -> bool:
        """Whether a seat of the host's account other than the named one is in the room."""
        return any(
            self.is_hosted_by(participant.account)
            for participant in self.participants
            if participant.participant_id != participant_id
        )

    @property
    def has_guests(self) -> bool:
        """Whether the call holds a seat other than the host's or a joined agent."""
        return bool(self.places) or any(not self.is_hosted_by(p.account) for p in self.participants)

    def is_hosted_by(self, account: Account) -> bool:
        return self.is_hosted_by_account_id(account.id)

    def is_hosted_by_account_id(self, account_id: str) -> bool:
        return account_id == self.host.id

    def has_free_seat_for(self, account: Account, seat_limit: int) -> bool:
        return self._has_free_place(host_place_reserved=not self.is_hosted_by(account), place_limit=seat_limit)

    def has_free_place_for_agent(self, place_limit: int) -> bool:
        return self._has_free_place(host_place_reserved=True, place_limit=place_limit)

    def _has_free_place(self, *, host_place_reserved: bool, place_limit: int) -> bool:
        """People and joined agents take the same places; an away host's place stays free for the host."""
        reserved = host_place_reserved and not self.host_present
        return self.participant_count + len(self.places) + int(reserved) < place_limit

    def check_admission(self, account: Account, seat_limit: int) -> None:
        """Refuse a seat of the account now: a locked room refuses all but its host, a full one all."""
        if self.locked and not self.is_hosted_by(account):
            raise RoomLockedError(self.room_id)
        if not self.has_free_seat_for(account, seat_limit):
            raise RoomFullError(self.room_id, seat_limit)

    def check_hosted_by(self, requester: Account) -> None:
        if not self.is_hosted_by(requester):
            raise NotHostError(self.room_id)

    def with_participant(self, participant: Participant) -> Room:
        return replace(self, participants=(*self.participants, participant))

    def without_participant(self, participant_id: str) -> Room:
        kept = tuple(p for p in self.participants if p.participant_id != participant_id)
        return replace(self, participants=kept)

    def with_lock(self, *, locked: bool) -> Room:
        return replace(self, locked=locked)

    def with_grace(self, grace: HostGrace | None) -> Room:
        return replace(self, grace=grace)

    def with_agent(self, place: AgentPlace, place_limit: int) -> Room:
        if not self.has_free_place_for_agent(place_limit):
            raise RoomFullError(self.room_id, place_limit)
        return replace(self, places=(*self.places, place))

    def without_agent(self, agent_id: str) -> Room:
        return replace(self, places=tuple(place for place in self.places if place.agent_id != agent_id))

    def departure_of(self, participant_id: str, generation: str) -> Departure:
        """What the room must hear once the seat leaves it; the one rule the leave, the announcement and a lapse's leave
        share."""
        leaver = next(p for p in self.participants if p.participant_id == participant_id)
        return Departure(
            room_id=self.room_id,
            peer_id=participant_id,
            name=leaver.account.name,
            remaining=tuple(pid for pid in self.participant_ids if pid != participant_id),
            host_left=self.is_hosted_by(leaver.account) and not self.host_present_without(participant_id),
            generation=generation,
        )

    def with_departure(self, departure: Departure) -> Room:
        return replace(self, departures=(*self.departures, departure))

    def without_departure(self, participant_id: str) -> Room:
        return replace(self, departures=tuple(d for d in self.departures if d.peer_id != participant_id))

    def departure_naming(self, participant_id: str) -> Departure | None:
        return next((d for d in self.departures if d.peer_id == participant_id), None)
