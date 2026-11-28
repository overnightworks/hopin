"""A call's rooms, seats and frames, in hopin's Redis, and the one script that commits each transition.

A record is an immutable dataclass of `hopin.rooms.models`; the codec lives here. Under `hopin:v1:call:`:

- `room:<id>`, `seat:<peer>` and `account:<id>` are hashes whose `v` field is the record's version, a compare-and-set
  counter; a room and a seat keep the rest of the record in `d`. A seat's `gen` is the process generation that owns it,
  `recv` the seq of the newest browser frame applied, `last` the seq of its newest logged frame and `floor` the seq its
  log reaches back to; those three change without a version, so a burst of frames meets no conflict.
- `seat:<peer>:log` is the seat's log, a stream whose entry id is the frame's seq, trimmed to `REPLAY_LOG_LIMIT`.
- `account:<id>:seats`, `hosted:<id>` and `generation:<g>:...` are the indices an account's cap, a person's export and
  erase, and a lost generation's lapse read.
- `commit:<id>` remembers a commit's result for `COMMIT_ID_SECONDS`, so a retry after a lost reply applies once.

A decision reads records, decides through their pure rules and hands the store a `Commit`; the script compares the
versions the decision read, checks the committer's lease and the acting seat's generation, writes and logs the frames.
It holds no rule beyond supersede, trim, the frame cap, those fences and idempotency.
"""

import asyncio
import contextlib
import json
import secrets
import string
from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import partial
from typing import TYPE_CHECKING, Final

import structlog
from redis.exceptions import RedisError
from tandem import Hold, Leases, Script, StoreError

from hopin.config.constants import (
    CALL_LAPSE_CLAIM_SECONDS,
    CALL_LEASE_FAMILY,
    CALL_LEASE_LASTS_SECONDS,
    COMMIT_ATTEMPTS,
    COMMIT_ID_SECONDS,
    REDIS_FAMILY_PREFIX,
    REPLAY_LOG_LIMIT,
    ROOM_ID_LENGTH,
    STORED_FRAME_MAX_BYTES,
)
from hopin.errors import RoomNotFoundError
from hopin.rooms.models import (
    AgentPlace,
    HostGrace,
    Park,
    Participant,
    Room,
    SeatRecord,
)
from hopin.signaling.call_format import (
    COMMIT_SCRIPT,
    account_key,
    account_seats_key,
    commit_key,
    decode_room,
    decode_seat,
    encode,
    encode_room,
    encode_seat,
    generation_key,
    hosted_key,
    log_key,
    room_index,
    room_key,
    seat_index,
    seat_key,
    supersede_of,
    text,
)
from hopin.signaling.frames import SENDER_KEY, SEQUENCE_KEY, TYPE_KEY, JsonObject, PeerId
from hopin.signaling.replay import HOLDING_KEY, SequencedFrame, Supersedes, superseded_by

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
    from datetime import timedelta

    from redis.asyncio import Redis as AsyncRedis

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# How long an account's version outlives its last change: it counts seat changes, and a version that expired meets no
# decision still reading it.
_ACCOUNT_VERSION_SECONDS: Final = 3600
_ROOM_ID_ALPHABET: Final = string.ascii_lowercase + string.digits


class CommitRefusal(StrEnum):
    LEASE = "lease"
    FENCE = "fence"
    FRAME_TOO_LARGE = "too_large"


class CommitRefusedError(Exception):
    """The script wrote nothing: the committer's lease is not live under its generation, the acting seat belongs to
    another generation, or a frame is beyond what a log keeps."""

    def __init__(self, refusal: CommitRefusal) -> None:
        super().__init__(refusal.value)
        self.refusal = refusal


class VersionConflictError(Exception):
    """A record the decision read has changed since: nothing was written."""


class DepartureAlreadyToldError(Exception):
    """The room has been told of the departure already, by the announcement or by a lapse."""


class NotServingError(Exception):
    """This process holds no lease yet, so it commits nothing."""


class ProcessLease:
    """This process's lease on the family of HopIn's processes, as the store fences its commits on: the lease's keys,
    this start's holder id and the generation the lease is held under now."""

    def __init__(self, leases: Leases, holder: str) -> None:
        self.leases = leases
        self.holder = holder
        self._hold: Hold | None = None

    def held_as(self, hold: Hold) -> None:
        """Commit under the generation of this hold, which its renewals move."""
        self._hold = hold

    @property
    def serving(self) -> bool:
        return self._hold is not None

    @property
    def generation(self) -> str:
        if self._hold is None:
            raise NotServingError
        return self._hold.generation


def call_leases(redis: AsyncRedis, lasts: timedelta | None = None, claim_for: timedelta | None = None) -> Leases:
    """The leases of HopIn's processes; every process of a deployment passes the same spans."""
    from datetime import timedelta

    return Leases(
        redis,
        key_prefix=REDIS_FAMILY_PREFIX,
        family=CALL_LEASE_FAMILY,
        lasts=lasts or timedelta(seconds=CALL_LEASE_LASTS_SECONDS),
        claim_for=claim_for or timedelta(seconds=CALL_LAPSE_CLAIM_SECONDS),
    )


# What stops a commit this process cannot make: the store answers or refuses, nothing is written.
COMMIT_FAILURES = (CommitRefusedError, VersionConflictError, NotServingError, StoreError, RedisError)


@dataclass(frozen=True)
class SeatOpened:
    peer_id: PeerId
    record: SeatRecord


@dataclass(frozen=True)
class SeatForgotten:
    """The seat ends, its record and its log with it; a seat already gone is left as it is."""

    peer_id: PeerId


@dataclass(frozen=True)
class SeatParked:
    peer_id: PeerId
    dropped_at: float


@dataclass(frozen=True)
class SeatTakenOver:
    """A resume gives the seat the hash of its new token and ends its park."""

    peer_id: PeerId
    token_hash: bytes


@dataclass(frozen=True)
class RoomCreated:
    room_id: str
    host_peer_id: PeerId


@dataclass(frozen=True)
class ParticipantAdded:
    room_id: str
    peer_id: PeerId


@dataclass(frozen=True)
class ParticipantRemoved:
    """The seat leaves the room, which keeps its departure until the room is told of it."""

    room_id: str
    peer_id: PeerId


@dataclass(frozen=True)
class RoomLockSet:
    room_id: str
    requester: PeerId
    locked: bool


@dataclass(frozen=True)
class RoomEnded:
    room_id: str
    peer_ids: tuple[PeerId, ...]


@dataclass(frozen=True)
class HostGraceStarted:
    room_id: str
    host_peer_id: PeerId
    deadline: float


@dataclass(frozen=True)
class HostGraceCleared:
    room_id: str


@dataclass(frozen=True)
class DepartureTold:
    """The room has heard of the seat's departure; refused when it has heard it already."""

    room_id: str
    peer_id: PeerId


type Edit = (
    SeatOpened
    | SeatForgotten
    | SeatParked
    | SeatTakenOver
    | RoomCreated
    | ParticipantAdded
    | ParticipantRemoved
    | RoomLockSet
    | RoomEnded
    | HostGraceStarted
    | HostGraceCleared
    | DepartureTold
)


@dataclass(frozen=True)
class Outbound:
    """A frame for one seat, and the earlier frames in that seat's log it supersedes."""

    peer_id: PeerId
    frame: JsonObject
    supersedes: Supersedes | None = None


@dataclass(frozen=True)
class Commit:
    """What one transition writes: its edits to rooms and seats, the agent places it takes and frees in `room_id`, the
    frames it logs, the seq of the browser frame it answers and the seat that acts, whose generation is fenced."""

    edits: tuple[Edit, ...] = ()
    frames: tuple[Outbound, ...] = ()
    room_id: str | None = None
    places_taken: tuple[str, ...] = ()
    places_released: tuple[str, ...] = ()
    acting: PeerId | None = None
    received: Mapping[PeerId, int] = field(default_factory=dict)


@dataclass(frozen=True)
class LogRead:
    """A seat's log after a seq, and whether the seat is parked and how far its log reaches back."""

    parked: bool
    floor: int
    frames: tuple[SequencedFrame, ...]


_FAILED_STATUSES: Final = {
    "lease": CommitRefusal.LEASE,
    "fence": CommitRefusal.FENCE,
    "too_large": CommitRefusal.FRAME_TOO_LARGE,
}


@dataclass
class _Action:
    """What a task's decision holds: the rooms it has locked and the records it has read."""

    held: frozenset[str]
    scope: dict[str, object]


@dataclass
class _RoomLock:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


def _acting_peers(edits: Sequence[Edit]) -> set[PeerId]:
    """The seats whose own act the edits are, whose generation must be the committer's; a leave, a forget, an end and an
    erase act for nobody."""
    acting: set[PeerId] = set()
    for edit in edits:
        match edit:
            case RoomCreated(_, peer_id) | ParticipantAdded(_, peer_id) | RoomLockSet(_, peer_id, _):
                acting.add(peer_id)
            case SeatParked(peer_id, _) | SeatTakenOver(peer_id, _):
                acting.add(peer_id)
            case _:
                pass
    return acting


class _Draft:
    """The records one commit changes, read through the store as the decision read them and changed by its edits."""

    def __init__(self, store: CallStore, request: Commit) -> None:
        self._store = store
        self._request = request
        self._generation = store.generation
        self.rooms: dict[str, Room | None] = {}
        self.room_reads: dict[str, Room | None] = {}
        self.seats: dict[str, SeatRecord | None] = {}
        self.seat_reads: dict[str, SeatRecord | None] = {}
        self.account_reads: dict[str, int] = {}
        self.account_bumps: set[str] = set()

    async def room(self, room_id: str) -> Room | None:
        if room_id not in self.rooms:
            self.room_reads[room_id] = self.rooms[room_id] = await self._store.find_room(room_id)
        return self.rooms[room_id]

    async def required_room(self, room_id: str) -> Room:
        room = await self.room(room_id)
        if room is None:
            raise RoomNotFoundError(room_id)
        return room

    async def seat(self, peer_id: str) -> SeatRecord | None:
        if peer_id not in self.seats:
            self.seat_reads[peer_id] = self.seats[peer_id] = await self._store.find_record(PeerId(peer_id))
        return self.seats[peer_id]

    async def required_seat(self, peer_id: str) -> SeatRecord:
        seat = await self.seat(peer_id)
        if seat is None:
            raise KeyError(peer_id)
        return seat

    async def bump_account(self, account_id: str) -> None:
        if account_id not in self.account_reads:
            self.account_reads[account_id] = await self._store.account_version(account_id)
        self.account_bumps.add(account_id)

    async def apply(self) -> None:
        for edit in self._request.edits:
            await self._apply(edit)
        room_id = self._request.room_id
        if room_id is None:
            if self._request.places_taken:  # pragma: no cover - a hook keeps places in its room
                raise ValueError("A transition of no room keeps no state")
            return
        for agent_id in self._request.places_taken:
            room = await self.required_room(room_id)
            self.rooms[room_id] = room.with_agent(AgentPlace(agent_id, self._generation), self._store.place_limit)
        for agent_id in self._request.places_released:
            holding = await self.room(room_id)
            if holding is not None:
                self.rooms[room_id] = holding.without_agent(agent_id)

    async def _apply(self, edit: Edit) -> None:
        await self._edit_handlers[type(edit)](self, edit)

    async def _open_seat(self, edit: SeatOpened) -> None:
        await self.seat(edit.peer_id)
        self.seats[edit.peer_id] = replace(edit.record, generation=self._generation)
        await self.bump_account(edit.record.account.id)

    async def _forget_seat(self, edit: SeatForgotten) -> None:
        forgotten = await self.seat(edit.peer_id)
        if forgotten is not None:
            self.seats[edit.peer_id] = None
            await self.bump_account(forgotten.account.id)

    async def _park_seat(self, edit: SeatParked) -> None:
        seat = await self.required_seat(edit.peer_id)
        self.seats[edit.peer_id] = replace(seat, park=Park(edit.dropped_at))
        await self.bump_account(seat.account.id)

    async def _take_over_seat(self, edit: SeatTakenOver) -> None:
        seat = await self.required_seat(edit.peer_id)
        self.seats[edit.peer_id] = replace(seat, park=None, token_hash=edit.token_hash)
        await self.bump_account(seat.account.id)

    async def _create_room(self, edit: RoomCreated) -> None:
        seat = await self.required_seat(edit.host_peer_id)
        if await self.room(edit.room_id) is not None:
            raise VersionConflictError
        host = Participant(edit.host_peer_id, seat.account)
        self.rooms[edit.room_id] = Room(room_id=edit.room_id, host=seat.account, participants=(host,))
        self.seats[edit.host_peer_id] = replace(seat, room_id=edit.room_id)

    async def _add_participant(self, edit: ParticipantAdded) -> None:
        room = await self.required_room(edit.room_id)
        seat = await self.required_seat(edit.peer_id)
        room.check_admission(seat.account, self._store.place_limit)
        self.rooms[edit.room_id] = room.with_participant(Participant(edit.peer_id, seat.account))
        self.seats[edit.peer_id] = replace(seat, room_id=edit.room_id)

    async def _remove_participant(self, edit: ParticipantRemoved) -> None:
        room = await self.required_room(edit.room_id)
        if edit.peer_id in room.participant_ids:
            departure = room.departure_of(edit.peer_id, self._generation)
            self.rooms[edit.room_id] = room.without_participant(edit.peer_id).with_departure(departure)

    async def _set_room_lock(self, edit: RoomLockSet) -> None:
        room = await self.required_room(edit.room_id)
        room.check_hosted_by((await self.required_seat(edit.requester)).account)
        self.rooms[edit.room_id] = room.with_lock(locked=edit.locked)

    async def _end_room(self, edit: RoomEnded) -> None:
        await self.room(edit.room_id)
        self.rooms[edit.room_id] = None
        for peer_id in edit.peer_ids:
            member = await self.seat(peer_id)
            if member is not None:
                self.seats[peer_id] = replace(member, room_id=None)

    async def _start_host_grace(self, edit: HostGraceStarted) -> None:
        room = await self.required_room(edit.room_id)
        self.rooms[edit.room_id] = room.with_grace(HostGrace(edit.host_peer_id, edit.deadline, self._generation))

    async def _clear_host_grace(self, edit: HostGraceCleared) -> None:
        graced = await self.room(edit.room_id)
        if graced is not None and graced.grace is not None:
            self.rooms[edit.room_id] = graced.with_grace(None)

    async def _tell_departure(self, edit: DepartureTold) -> None:
        telling = await self.room(edit.room_id)
        if telling is None or telling.departure_naming(edit.peer_id) is None:
            raise DepartureAlreadyToldError
        self.rooms[edit.room_id] = telling.without_departure(edit.peer_id)

    _edit_handlers: Final[Mapping[type, Callable[..., Awaitable[None]]]] = {
        SeatOpened: _open_seat,
        SeatForgotten: _forget_seat,
        SeatParked: _park_seat,
        SeatTakenOver: _take_over_seat,
        RoomCreated: _create_room,
        ParticipantAdded: _add_participant,
        ParticipantRemoved: _remove_participant,
        RoomLockSet: _set_room_lock,
        RoomEnded: _end_room,
        HostGraceStarted: _start_host_grace,
        HostGraceCleared: _clear_host_grace,
        DepartureTold: _tell_departure,
    }

    def command(self) -> dict[str, object]:
        """The script's command for the changes made: the versions it compares and the records and indices it writes."""
        command = _CommandParts()
        self._add_room_changes(command)
        self._add_seat_changes(command)
        self._add_account_bumps(command)
        return command.as_script_command()

    def _add_room_changes(self, command: _CommandParts) -> None:
        for room_id, room in self.rooms.items():
            before = self.room_reads[room_id]
            if room == before:
                continue
            version = 0 if before is None else before.version
            command.reads[room_key(room_id)] = version
            command.before_index |= room_index(before)
            command.after_index |= room_index(room)
            if room is None:
                command.deletes.append(room_key(room_id))
            else:
                fields = [["v", version + 1], ["d", encode_room(room)]]
                command.writes.append({"key": room_key(room_id), "fields": fields})

    def _add_seat_changes(self, command: _CommandParts) -> None:
        for peer_id, seat in self.seats.items():
            before = self.seat_reads[peer_id]
            if seat == before:
                continue
            version = 0 if before is None else before.version
            command.reads[seat_key(peer_id)] = version
            command.before_index |= seat_index(peer_id, before)
            command.after_index |= seat_index(peer_id, seat)
            if seat is None:
                command.deletes += [seat_key(peer_id), log_key(peer_id)]
            else:
                fields = [["v", version + 1], ["d", encode_seat(seat)], ["gen", seat.generation]]
                command.writes.append({"key": seat_key(peer_id), "fields": fields})

    def _add_account_bumps(self, command: _CommandParts) -> None:
        for account_id, version in self.account_reads.items():
            command.reads[account_key(account_id)] = version
        for account_id in self.account_bumps:
            fields = [["v", self.account_reads[account_id] + 1]]
            command.writes.append({"key": account_key(account_id), "fields": fields})
            command.expires.append([account_key(account_id), _ACCOUNT_VERSION_SECONDS])


@dataclass
class _CommandParts:
    """The script's command as its changes add to it."""

    reads: dict[str, int] = field(default_factory=dict)
    writes: list[dict[str, object]] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)
    expires: list[list[object]] = field(default_factory=list)
    before_index: set[tuple[str, str]] = field(default_factory=set)
    after_index: set[tuple[str, str]] = field(default_factory=set)

    def as_script_command(self) -> dict[str, object]:
        return {
            "reads": [[key, version] for key, version in self.reads.items()],
            "writes": self.writes,
            "deletes": self.deletes,
            "removes": [list(entry) for entry in sorted(self.before_index - self.after_index)],
            "adds": [list(entry) for entry in sorted(self.after_index - self.before_index)],
            "expires": self.expires,
        }


def _room_and_agent(member: bytes | str) -> tuple[str, str]:
    room_id, _, agent_id = text(member).partition(":")
    return room_id, agent_id


class CallStore:
    """The calls of one deployment, in its Redis: reads of the records and the one commit that writes them."""

    def __init__(
        self,
        redis: AsyncRedis,
        lease: ProcessLease,
        *,
        place_limit: int,
        log_limit: int = REPLAY_LOG_LIMIT,
    ) -> None:
        self._redis = redis
        self._lease = lease
        self.place_limit = place_limit
        self._log_limit = log_limit
        self._commit = Script(redis, COMMIT_SCRIPT)
        self._actions: dict[asyncio.Task[object] | None, _Action] = {}
        self._room_locks: dict[str, _RoomLock] = {}

    @property
    def generation(self) -> str:
        return self._lease.generation

    # --- the scope of a decision: one lock per room within a process, and the records the decision read ---

    @contextlib.asynccontextmanager
    async def action(self, room_id: str | None) -> AsyncIterator[dict[str, object]]:
        """Run a decision in a room: no other action of this process runs in the room meanwhile, the records it reads
        are read once, and its commit writes against the versions it read. A nested action in a room its task holds
        runs inside the held one, so a callback of a commit passes through the lock; a task it starts holds nothing."""
        task = asyncio.current_task()
        current = self._actions.get(task)
        if current is not None and (room_id is None or room_id in current.held):
            yield current.scope
            return
        entry = None if room_id is None else self._room_locks.setdefault(room_id, _RoomLock())
        held = frozenset() if current is None else current.held
        if entry is not None:
            entry.users += 1
        try:
            async with entry.lock if entry is not None else contextlib.nullcontext():
                self._actions[task] = _Action(held if room_id is None else held | {room_id}, {})
                try:
                    yield self._actions[task].scope
                finally:
                    if current is None:
                        del self._actions[task]
                    else:
                        self._actions[task] = current
        finally:
            if entry is not None:
                entry.users -= 1
                if entry.users == 0 and room_id is not None:
                    del self._room_locks[room_id]

    async def deciding[Outcome](self, room_id: str | None, decide: Callable[[], Awaitable[Outcome]]) -> Outcome:
        """Decide and commit in a room, reading the records afresh and deciding again while another commit changes what
        the decision read; the third conflict fails loud."""
        async with self.action(room_id) as scope:
            for attempt in range(COMMIT_ATTEMPTS):
                scope.clear()
                try:
                    return await decide()
                except VersionConflictError:
                    if attempt == COMMIT_ATTEMPTS - 1:
                        raise
        raise AssertionError  # pragma: no cover - the loop returns or raises

    async def _read[Record](self, key: str, load: Callable[[], Awaitable[Record]]) -> Record:
        scope = self._scope()
        if scope is not None and key in scope:
            return scope[key]  # type: ignore[return-value]  # the key names one kind of record
        record = await load()
        if scope is not None:
            scope[key] = record
        return record

    def _scope(self) -> dict[str, object] | None:
        action = self._actions.get(asyncio.current_task())
        return None if action is None else action.scope

    # --- reads ---

    async def find_room(self, room_id: str) -> Room | None:
        return await self._read(room_key(room_id), partial(self._load_room, room_id))

    async def get_room(self, room_id: str) -> Room:
        room = await self.find_room(room_id)
        if room is None:
            raise RoomNotFoundError(room_id)
        return room

    async def find_record(self, peer_id: PeerId) -> SeatRecord | None:
        return await self._read(seat_key(peer_id), partial(self._load_seat, peer_id))

    async def record_of(self, peer_id: PeerId) -> SeatRecord:
        record = await self.find_record(peer_id)
        if record is None:
            raise KeyError(peer_id)
        return record

    async def room_of(self, peer_id: PeerId) -> str | None:
        record = await self.find_record(peer_id)
        return None if record is None else record.room_id

    async def account_version(self, account_id: str) -> int:
        return await self._read(account_key(account_id), partial(self._load_account_version, account_id))

    async def held_by(self, account_id: str) -> tuple[tuple[PeerId, SeatRecord], ...]:
        """The seats an account holds, parked ones included."""
        await self.account_version(account_id)
        return await self._seats_in(account_seats_key(account_id))

    async def hosted_by(self, account_id: str) -> tuple[Room, ...]:
        rooms = [await self.find_room(text(room_id)) for room_id in await self._redis.smembers(hosted_key(account_id))]
        return tuple(room for room in rooms if room is not None)

    async def seats_of_generation(self, generation: str) -> tuple[tuple[PeerId, SeatRecord], ...]:
        return await self._seats_in(generation_key(generation, "seats"))

    async def places_of_generation(self, generation: str) -> tuple[tuple[str, str], ...]:
        """The room and the agent of each place the generation took."""
        members = await self._redis.smembers(generation_key(generation, "places"))
        return tuple(_room_and_agent(member) for member in members)

    async def graces_of_generation(self, generation: str) -> tuple[str, ...]:
        return await self._names_in(generation_key(generation, "graces"))

    async def departures_of_generation(self, generation: str) -> tuple[str, ...]:
        return await self._names_in(generation_key(generation, "departures"))

    async def log_length(self, peer_id: PeerId) -> int:
        return int(await self._redis.xlen(log_key(peer_id)))

    async def read_log(self, peer_id: PeerId, after_seq: int) -> LogRead | None:
        """The seat's logged frames after the seq, or nothing for a seat that is gone."""
        async with self._redis.pipeline(transaction=False) as pipeline:
            pipeline.hget(seat_key(peer_id), "d")
            pipeline.hget(seat_key(peer_id), "floor")
            pipeline.xrange(log_key(peer_id), min=f"{after_seq + 1}-0", max="+")
            stored, floor, entries = await pipeline.execute()
        if stored is None:
            return None
        frames = tuple(
            SequencedFrame(
                int(entry_id.split(b"-")[0]),
                {**json.loads(fields[b"frame"]), SEQUENCE_KEY: int(entry_id.split(b"-")[0])},
            )
            for entry_id, fields in entries
        )
        return LogRead(parked=json.loads(stored)["park"] is not None, floor=int(floor or 0), frames=frames)

    async def _seats_in(self, index: str) -> tuple[tuple[PeerId, SeatRecord], ...]:
        peers = [PeerId(text(member)) for member in await self._redis.smembers(index)]
        records = await asyncio.gather(*(self.find_record(peer) for peer in peers))
        return tuple((peer, record) for peer, record in zip(peers, records, strict=True) if record is not None)

    async def _names_in(self, index: str) -> tuple[str, ...]:
        return tuple(text(member) for member in await self._redis.smembers(index))

    async def _load_room(self, room_id: str) -> Room | None:
        fields = await self._redis.hgetall(room_key(room_id))
        return None if not fields else decode_room(room_id, int(fields[b"v"]), fields[b"d"])

    async def _load_seat(self, peer_id: PeerId) -> SeatRecord | None:
        fields = await self._redis.hgetall(seat_key(peer_id))
        return None if not fields else decode_seat(fields)

    async def _load_account_version(self, account_id: str) -> int:
        return int(await self._redis.hget(account_key(account_id), "v") or 0)

    async def new_room_id(self) -> str:
        """An id no room holds now."""
        while True:
            room_id = "".join(secrets.choice(_ROOM_ID_ALPHABET) for _ in range(ROOM_ID_LENGTH))
            if await self.find_room(room_id) is None:
                return room_id

    # --- the commit ---

    async def commit(self, request: Commit) -> tuple[PeerId, ...]:
        """Write the transition and log its frames, and name the seats that have a frame to be written. Nothing is
        written when the committer's lease is not live under its generation, when the acting seat belongs to another
        generation, when a frame is beyond `STORED_FRAME_MAX_BYTES` or when a record the decision read has changed."""
        draft = _Draft(self, request)
        await draft.apply()
        command = draft.command()
        commit_id = secrets.token_hex(16)
        frames = []
        for outbound in request.frames:
            frame = outbound.frame
            supersedes = superseded_by(frame, outbound.supersedes)
            frames.append(
                {
                    "seat": seat_key(outbound.peer_id),
                    "log": log_key(outbound.peer_id),
                    "peer": outbound.peer_id,
                    "type": frame[TYPE_KEY],
                    "sender": str(frame.get(SENDER_KEY) or ""),
                    "agent": str(frame.get(HOLDING_KEY) or ""),
                    "text": encode({key: value for key, value in frame.items() if key != SEQUENCE_KEY}),
                    **({} if supersedes is None else {"supersede": supersede_of(supersedes)}),
                }
            )
        fenced = {request.acting} if request.acting is not None else set()
        fenced |= _acting_peers(request.edits)
        fenced |= set(request.received)
        command |= {
            "fences": [seat_key(peer_id) for peer_id in sorted(fenced)],
            "received": [[seat_key(peer_id), seq] for peer_id, seq in request.received.items()],
            "frames": frames,
            "limit": self._log_limit,
            "max_bytes": STORED_FRAME_MAX_BYTES,
        }
        reply = json.loads(await self._run(commit_id, command))
        status = reply["status"]
        if status == "conflict":
            raise VersionConflictError
        if status != "ok":
            await logger.awarning("call_commit.refused", reason=status, frame_types=[frame["type"] for frame in frames])
            raise CommitRefusedError(_FAILED_STATUSES[status])
        self._remember(draft)
        return tuple(dict.fromkeys(PeerId(peer_id) for peer_id in reply.get("woken") or ()))

    async def _run(self, commit_id: str, command: dict[str, object]) -> bytes:
        keys = self._lease.leases.keys
        lasts_ms = int(self._lease.leases.lasts.total_seconds() * 1000)
        arguments: list[str | int | bytes] = [
            self._lease.holder,
            lasts_ms,
            self.generation,
            encode(command),
            COMMIT_ID_SECONDS,
        ]
        names = [keys.epoch, keys.expiry, keys.generation, commit_key(commit_id)]
        return await self._commit.run(names, arguments)  # type: ignore[return-value]  # the script replies JSON text

    def _remember(self, draft: _Draft) -> None:
        """The scope reads what the commit wrote, so a later decision of the same action builds on it."""
        scope = self._scope()
        if scope is None:
            return
        for room_id, room in draft.rooms.items():
            before = draft.room_reads[room_id]
            if room != before:
                scope[room_key(room_id)] = (
                    None if room is None else replace(room, version=(0 if before is None else before.version) + 1)
                )
        for peer_id, seat in draft.seats.items():
            before_seat = draft.seat_reads[peer_id]
            if seat != before_seat:
                scope[seat_key(peer_id)] = (
                    None
                    if seat is None
                    else replace(seat, version=(0 if before_seat is None else before_seat.version) + 1)
                )
        for account_id in draft.account_bumps:
            scope[account_key(account_id)] = draft.account_reads[account_id] + 1
