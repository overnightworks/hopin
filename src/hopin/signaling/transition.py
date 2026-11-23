"""A change of a call as one transition: load the room's view, apply a feature's hook to it, then commit the hook's
state and frames together and wake the seats it sent to. The call core's own actions (a seat opening, a join, a lock, a
leave, a park, a resume, an end) are transitions too, made of edits to rooms and seats beside the frames they send. The
features and the call core commit through one store, and the commit is the only writer of rooms, seats and seat logs.
Work a transition dispatched runs beside the call, and its results re-enter the room as transitions of their own.

Each hook reads the room's view (`RoomView`: its seats and their accounts and names, its hosts, its parked seats,
whether an agent place is free and the feature's own `Section` of state in that room), puts state, takes or frees agent
places and sends frames only through its `Transition`, and does no other work. The store (`InMemoryCallStore`, one
process's rooms and seats) commits both in one synchronous step, and a hook that raises commits neither. Only then are
the seats it sent to woken, each writing its logged frames in the order of their seq.

- A transition may follow itself with another hook, which reads the room as the first one left it; the seats of both
  are woken together.
- A seat's departure and each feature's `seat_left` commit together before any seat is woken, so the room never hears
  an outdated frame after the leave.
- A frame may name the frames of its seat's queue it supersedes (`Supersedes`, such as a newer answer count an older
  one); the commit drops those not yet being written, and a frame whose write has begun is written all the same.
- The changes a feature starts itself run on the same transitions (`FeatureTransitions`, handed to the feature by
  `attach`). A step whose writes must not hold its caller back, such as showing a started sentence, runs in a turn of
  its own (`in_own_turn`).
- A hook's other work, a call to another service or to a seat's socket, is `Work` the transition dispatches: it runs on
  the process that holds its seat's socket, reaches a connection only there (`LiveSeats.connection_of`), and its
  results re-enter the room as transitions of their own.
"""

import asyncio
from dataclasses import dataclass, field, replace
from functools import partial
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, assert_never, cast, get_args

import structlog

from hopin.config.constants import RESUME_WINDOW_SECONDS
from hopin.rooms.models import HostGrace
from hopin.signaling.frames import PeerId
from hopin.signaling.scheduler import DeadlineKind
from hopin.signaling.seats import Park, Seats

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Mapping, Sequence

    from starlette.requests import HTTPConnection

    from hopin.rooms.manager import RoomManager
    from hopin.signaling.frames import JsonObject
    from hopin.signaling.replay import Supersedes
    from hopin.signaling.scheduler import Deadlines
    from hopin.signaling.seats import SeatRecord, SeatSocket

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


@dataclass(frozen=True)
class Section[State]:
    """A feature's own state in each room, named once by the feature that owns it."""

    name: str


@dataclass(frozen=True)
class RoomView:
    """One room as its transition loaded it; a seat outside any room sees a view of no room."""

    room_id: str | None = None
    members: tuple[PeerId, ...] = ()
    hosts: frozenset[PeerId] = frozenset()
    names: Mapping[PeerId, str] = field(default_factory=lambda: MappingProxyType({}))
    accounts: Mapping[PeerId, str] = field(default_factory=lambda: MappingProxyType({}))
    parked: frozenset[PeerId] = frozenset()
    has_free_place: bool = False
    """Whether the room has a place left for another joined agent."""
    sections: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def room_of(self, peer_id: PeerId) -> str | None:
        return self.room_id if peer_id in self.members else None

    def name_of(self, peer_id: PeerId) -> str:
        """The name the call shows for a seat of the room, or for the seat whose leave the transition hears."""
        return self.names[peer_id]

    def naming(self, peer_id: PeerId, name: str) -> RoomView:
        """This view with the name of a seat it no longer holds, for the transition that hears that seat's leave."""
        names = MappingProxyType({**self.names, peer_id: name})
        # A field-by-field copy drops each field it leaves out; `replace` keeps all and returns its argument type.
        return replace(self, names=names)  # NOSONAR(S5886) replace returns a RoomView here

    def is_host(self, peer_id: PeerId) -> bool:
        """Whether the seat's account hosts the room."""
        return peer_id in self.hosts

    def seats_in(self, account_id: str) -> tuple[PeerId, ...]:
        """The seats the account holds in the room, parked ones included."""
        return tuple(peer_id for peer_id in self.members if self.accounts[peer_id] == account_id)

    def is_parked(self, peer_id: PeerId) -> bool:
        """Whether the seat of the room has lost its socket and waits for a resume."""
        return peer_id in self.parked

    def section[State](self, section: Section[State]) -> State | None:
        # Only `Transition.put` writes a section, and it takes the section's own state type.
        return cast("State | None", self.sections.get(section.name))


@dataclass(frozen=True)
class Work:
    """A call to another service or to a seat's socket, which a hook never makes itself: a transition dispatches it,
    and it runs on the process that holds the socket of `seat`. `key` names it among the feature's work."""

    key: str
    seat: PeerId


@dataclass(frozen=True)
class Outbound:
    """A frame for one seat, and the earlier frames in that seat's queue it supersedes."""

    peer_id: PeerId
    frame: JsonObject
    supersedes: Supersedes | None = None


@dataclass
class Change:
    """What a hook that sends nothing changes: the state of its sections, the agent places it takes or frees and the
    work it dispatches or stops, applied only by the commit."""

    puts: dict[str, object] = field(default_factory=dict)
    dispatched: list[Work] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    seated_agents: list[str] = field(default_factory=list)
    released_agents: list[str] = field(default_factory=list)

    def put[State](self, section: Section[State], state: State) -> None:
        self.puts[section.name] = state

    def take_agent_place(self, agent_id: str) -> None:
        """Seat the joined agent among the room's people; the view must have a free place for it."""
        self.seated_agents.append(agent_id)

    def release_agent_place(self, agent_id: str) -> None:
        """Free the agent's place; a room that has ended holds none."""
        self.released_agents.append(agent_id)

    def dispatch(self, work: Work) -> None:
        """Run the work once the change has committed."""
        self.dispatched.append(work)

    def stop(self, key: str) -> None:
        """Stop the feature's work of that key, if it still runs, once the change has committed."""
        self.stopped.append(key)

    def outbound(self) -> Sequence[Outbound]:
        """The frames the commit logs: none for a change that cannot send."""
        return ()


@dataclass
class Transition(Change):
    """What one hook changes and the frames it sends, applied only by the commit; a hook it follows with runs on the
    room's view once it has committed."""

    frames: list[Outbound] = field(default_factory=list)
    followers: list[Callable[[RoomView, Transition], None]] = field(default_factory=list)
    broken_write_event: str | None = None

    def send(self, peer_id: PeerId, frame: JsonObject, supersedes: Supersedes | None = None) -> None:
        """Send as the call core sends: logged for a resume, held while the seat is parked or replaying. The commit
        drops the frames it supersedes from the seat's queue, so a seat a slow write holds back never gets an outdated
        one; a frame whose write has begun is written all the same."""
        self.frames.append(Outbound(peer_id, frame, supersedes))

    def follow(self, hook: Callable[[RoomView, Transition], None]) -> None:
        """Apply the hook to the room's view as a transition of its own once this one has committed, so it reads what
        this one changed; the seats of both are woken together."""
        self.followers.append(hook)

    def log_broken_writes_as(self, event: str) -> None:
        """Log a seat's broken write of this transition's frames under the event, by the error's type alone, instead of
        raising it; every other seat has its frames either way."""
        self.broken_write_event = event

    def outbound(self) -> Sequence[Outbound]:
        return self.frames


@dataclass(frozen=True)
class SeatOpened:
    peer_id: PeerId
    record: SeatRecord
    socket: SeatSocket


@dataclass(frozen=True)
class SeatForgotten:
    """The seat ends, its record and its socket with it; a seat already gone is left as it is."""

    peer_id: PeerId


@dataclass(frozen=True)
class SeatParked:
    peer_id: PeerId


@dataclass(frozen=True)
class SeatTakenOver:
    """A resume gives the seat its new socket and the hash of its new token, and ends its park."""

    peer_id: PeerId
    token_hash: bytes
    socket: SeatSocket


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


type SeatEdit = SeatOpened | SeatForgotten | SeatParked | SeatTakenOver
_SEAT_EDITS = get_args(SeatEdit.__value__)
type RoomEdit = (
    RoomCreated | ParticipantAdded | ParticipantRemoved | RoomLockSet | RoomEnded | HostGraceStarted | HostGraceCleared
)
type Edit = SeatEdit | RoomEdit


@dataclass
class CoreTransition(Transition):
    """A transition of the call core: the edits it makes to rooms and seats, applied in order by the commit, beside the
    frames it sends. An edit that refuses, such as a join of a locked room, leaves the edits after it unapplied."""

    edits: list[Edit] = field(default_factory=list)

    def edit(self, edit: Edit) -> None:
        self.edits.append(edit)


@dataclass(frozen=True)
class Committed:
    """A change the store has committed: the seats it logged frames for, which are still to be woken, and the event a
    broken write of them is logged under, none to raise it."""

    woken: tuple[PeerId, ...]
    broken_write_event: str | None = None


class LiveSeats(Protocol):
    """The seats whose sockets this process holds."""

    def connection_of(self, peer_id: PeerId) -> HTTPConnection:
        """The connection that serves the seat now, carrying its sign-in; the seat's socket is held here."""


class WorkRoom(Protocol):
    """The room a work was dispatched in, which the work's results re-enter."""

    @property
    def room_id(self) -> str:
        """The room's id."""

    async def view(self) -> RoomView:
        """The room's view now; a room that has ended is the view of no room."""

    async def apply[Outcome](self, hook: Callable[[RoomView, Transition], Outcome]) -> Outcome:
        """Apply the hook to the room's view now as one transition, commit it and wake its seats."""


class InMemoryCallStore:
    """A process's calls behind the transition contract: the rooms and seats the call core keeps and the features'
    sections. A commit applies in one synchronous step, so no other transition runs between its load and its commit."""

    def __init__(
        self,
        room_manager: RoomManager,
        deadlines: Deadlines,
        on_room_ended: Callable[[str], None],
        on_seat_changed: Callable[[PeerId], None],
    ) -> None:
        self._room_manager = room_manager
        self._deadlines = deadlines
        self._on_room_ended = on_room_ended
        self._on_seat_changed = on_seat_changed
        self._records: dict[PeerId, SeatRecord] = {}
        self._seats = Seats(MappingProxyType(self._records))
        self._sections: dict[str, dict[str, object]] = {}
        self._unwritten_seqs: dict[PeerId, tuple[int, asyncio.Task[object] | None]] = {}

    @property
    def seats(self) -> Seats:
        return self._seats

    def find_record(self, peer_id: PeerId) -> SeatRecord | None:
        return self._records.get(peer_id)

    def record_of(self, peer_id: PeerId) -> SeatRecord:
        return self._records[peer_id]

    def held_by(self, account_id: str) -> tuple[tuple[PeerId, SeatRecord], ...]:
        """The seats an account holds, parked ones included."""
        return tuple((peer_id, record) for peer_id, record in self._records.items() if record.account.id == account_id)

    def room_of(self, peer_id: PeerId) -> str | None:
        record = self._records.get(peer_id)
        return None if record is None else record.room_id

    def load(self, room_id: str | None) -> RoomView:
        """The room's view now; an ended room or none is the view of no room."""
        room = None if room_id is None else self._room_manager.find_room(room_id)
        if room is None:
            return RoomView()
        records = self._records
        members = tuple(PeerId(participant_id) for participant_id in room.participant_ids)
        return RoomView(
            room_id=room.room_id,
            members=members,
            hosts=frozenset(peer_id for peer_id in members if room.is_hosted_by(records[peer_id].account)),
            names=MappingProxyType({peer_id: records[peer_id].account.name for peer_id in members}),
            accounts=MappingProxyType({peer_id: records[peer_id].account.id for peer_id in members}),
            parked=frozenset(peer_id for peer_id in members if records[peer_id].park is not None),
            has_free_place=self._room_manager.has_free_place_for_agent(room.room_id),
            sections=MappingProxyType(dict(self._sections.get(room.room_id, {}))),
        )

    def load_seat(self, peer_id: PeerId) -> RoomView:
        """The view of the room the seat sits in."""
        return self.load(self.room_of(peer_id))

    def receive(self, peer_id: PeerId, seq: int) -> None:
        """Note the seq of the browser's frame about to be applied. Only a commit made by the task that handles the
        frame writes it, after the frame's edits applied, so a frame is never counted received without its effect and
        another seat's commit never takes it. A frame that changes nothing is settled by `settle_received`."""
        self._unwritten_seqs[peer_id] = (seq, asyncio.current_task())

    def settle_received(self, peer_id: PeerId) -> None:
        """Commit the seq of a frame that no commit has written, which changed nothing."""
        if peer_id in self._unwritten_seqs:
            self.commit_edits(CoreTransition())

    def commit(self, view: RoomView, change: Change) -> tuple[PeerId, ...]:
        """Write the change's edits, sections and agent places and log each frame it sends for its seat, and name the
        seats to wake. The seq of the browser frame this task handles is written once the edits applied, so a refused
        edit leaves it to the commit that tells the browser of the refusal."""
        if isinstance(change, CoreTransition):
            for edit in change.edits:
                self._apply(edit)
        self._write_received_seqs()
        if view.room_id is None:
            if change.puts or change.seated_agents:  # pragma: no cover - a hook keeps state only in its room
                raise ValueError("A transition of no room keeps no state")
        else:
            if change.puts:
                self._sections.setdefault(view.room_id, {}).update(change.puts)
            for agent_id in change.seated_agents:
                self._room_manager.add_agent(view.room_id, agent_id)
            for agent_id in change.released_agents:
                self._room_manager.remove_agent(view.room_id, agent_id)
        return tuple(
            dict.fromkeys(
                outbound.peer_id
                for outbound in change.outbound()
                if self._log(outbound.peer_id, outbound.frame, outbound.supersedes)
            )
        )

    def commit_edits(self, transition: CoreTransition) -> None:
        """Commit a transition of the call core that sends nothing, so no seat waits to be woken."""
        self.commit(RoomView(), transition)

    async def apply(self, transition: CoreTransition) -> None:
        """Commit a transition of the call core and wake the seats it sent to."""
        await self.wake_committed([Committed(self.commit(RoomView(), transition))])

    def _write_received_seqs(self) -> None:
        handling = asyncio.current_task()
        for peer_id in [peer_id for peer_id, (_, task) in self._unwritten_seqs.items() if task is handling]:
            seq, _ = self._unwritten_seqs.pop(peer_id)
            self._records[peer_id].received_seq = seq

    def _log(self, peer_id: PeerId, frame: JsonObject, supersedes: Supersedes | None) -> bool:
        """Give the frame the seat's next seq in its log, dropping the frames it supersedes there, and say whether the
        seat is there to have it."""
        record = self._records.get(peer_id)
        if record is None:
            return False
        record.log.append(frame, supersedes)
        return True

    def _apply(self, edit: Edit) -> None:
        if isinstance(edit, _SEAT_EDITS):
            self._apply_to_seats(cast("SeatEdit", edit))
        else:
            self._apply_to_rooms(cast("RoomEdit", edit))

    def _apply_to_seats(self, edit: SeatEdit) -> None:
        match edit:
            case SeatOpened(peer_id, record, socket):
                self._records[peer_id] = record
                self._seats.attach(peer_id, socket)
            case SeatForgotten(peer_id):
                forgotten = self._records.pop(peer_id, None)
                self._seats.detach(peer_id)
                if forgotten is not None and forgotten.park is not None:
                    self._deadlines.cancel(DeadlineKind.RESUME_WINDOW, peer_id)
            case SeatParked(peer_id):
                self._deadlines.set(DeadlineKind.RESUME_WINDOW, peer_id, RESUME_WINDOW_SECONDS)
                self._records[peer_id].park = Park(dropped_at=self._deadlines.now())
                self._on_seat_changed(peer_id)
            case SeatTakenOver(peer_id, token_hash, socket):
                record = self._records[peer_id]
                parked = record.park is not None
                if parked:
                    self._deadlines.cancel(DeadlineKind.RESUME_WINDOW, peer_id)
                    record.park = None
                record.token_hash = token_hash
                self._seats.attach(peer_id, socket)
                if parked:
                    self._on_seat_changed(peer_id)
            case _:  # pragma: no cover - the type checker proves the match exhaustive
                assert_never(edit)

    def _apply_to_rooms(self, edit: RoomEdit) -> None:
        match edit:
            case RoomCreated(room_id, host_peer_id):
                record = self._records[host_peer_id]
                self._room_manager.create_room(host_peer_id, record.account, room_id)
                record.room_id = room_id
            case ParticipantAdded(room_id, peer_id):
                record = self._records[peer_id]
                self._room_manager.add_participant(room_id, peer_id, record.account)
                record.room_id = room_id
            case ParticipantRemoved(room_id, peer_id):
                self._room_manager.remove_participant(room_id, peer_id)
                self._on_seat_changed(peer_id)
            case RoomLockSet(room_id, requester, locked):
                self._room_manager.set_room_locked(room_id, self._records[requester].account, locked=locked)
            case RoomEnded(room_id, peer_ids):
                self._clear_host_grace(room_id)
                for peer_id in peer_ids:
                    self._records[peer_id].room_id = None
                self._room_manager.remove_room(room_id)
                self._on_room_ended(room_id)
            case HostGraceStarted(room_id, host_peer_id, deadline):
                self._deadlines.set(DeadlineKind.HOST_GRACE, room_id, deadline - self._deadlines.now())
                self._room_manager.set_host_grace(room_id, HostGrace(host_peer_id=host_peer_id, deadline=deadline))
            case HostGraceCleared(room_id):
                self._clear_host_grace(room_id)
            case _:  # pragma: no cover - the type checker proves the match exhaustive
                assert_never(edit)

    def _clear_host_grace(self, room_id: str) -> None:
        room = self._room_manager.find_room(room_id)
        if room is not None and room.grace is not None:
            self._deadlines.cancel(DeadlineKind.HOST_GRACE, room_id)
            self._room_manager.set_host_grace(room_id, None)

    async def wake_committed(self, committed: Sequence[Committed]) -> None:
        """Wake the seats of transitions committed together once every one of them is logged, so a frame of one
        supersedes the other's before any is written. A broken write raises unless each transition names the event it
        is logged under."""
        woken = tuple(dict.fromkeys(peer_id for each in committed for peer_id in each.woken))
        broken_writes = await self.wake(woken)
        events = [each.broken_write_event for each in committed]
        if not broken_writes:
            return
        if None in events:
            raise broken_writes[0]
        for error in broken_writes:
            # Only the type is logged, never a frame's text.
            logger.error(events[0], error_type=type(error).__name__)

    async def wake(self, peer_ids: tuple[PeerId, ...]) -> list[Exception]:
        """Write the woken seats' logged frames to their sockets, and name the writes that broke."""
        broken_writes: list[Exception] = []
        for peer_id in peer_ids:
            delivered = False
            while not delivered:
                try:
                    await self._seats.deliver(peer_id)
                    delivered = True
                except Exception as error:  # A broken write has used up its frame, so the next try writes the next one.
                    broken_writes.append(error)
        return broken_writes

    def end(self, room_id: str) -> None:
        """Drop the sections of a room that has ended."""
        self._sections.pop(room_id, None)

    def end_all(self) -> None:
        """Drop every room's sections, as the process's calls end."""
        self._sections.clear()


@dataclass(eq=False)
class _RunningWork:
    room_id: str
    task: asyncio.Task[None]


class Worker(Protocol):
    """What runs the work a feature's transitions dispatch."""

    @property
    def name(self) -> str:
        """The feature's name."""

    async def run_work(self, work: Work, live_seats: LiveSeats, room: WorkRoom) -> None:
        """As `TransitionFeature.run_work`."""

    def stop_work(self, key: str) -> None:
        """As `TransitionFeature.stop_work`."""


class FeatureTransitions:
    """One feature's transitions: each loads a room's view, applies a hook to it and commits what the hook changed,
    and the work they dispatch runs here, where the seats' sockets are held."""

    def __init__(self, worker: Worker, store: InMemoryCallStore, live_seats: LiveSeats) -> None:
        self._worker = worker
        self._store = store
        self._live_seats = live_seats
        self._works: dict[str, _RunningWork] = {}
        self._steps: set[asyncio.Task[None]] = set()

    def view(self, room_id: str | None) -> RoomView:
        """The room's view now; an ended room or none is the view of no room."""
        return self._store.load(room_id)

    def seat_view(self, peer_id: PeerId) -> RoomView:
        """The view of the room the seat sits in."""
        return self._store.load_seat(peer_id)

    def is_working(self, key: str) -> bool:
        """Whether the work of that key runs."""
        return key in self._works

    async def apply[Outcome](self, room_id: str | None, hook: Callable[[RoomView, Transition], Outcome]) -> Outcome:
        """Apply the hook to the room's view now as one transition, commit it and wake its seats."""
        [outcome] = await self.apply_each([room_id], hook)
        return outcome

    async def apply_each[Outcome](
        self, room_ids: Sequence[str | None], hook: Callable[[RoomView, Transition], Outcome]
    ) -> list[Outcome]:
        """Apply the hook to each room's view as one transition per room, and commit every one of them before any seat
        is woken."""
        outcomes: list[Outcome] = []
        committed: list[Committed] = []
        try:
            for room_id in room_ids:
                view = self._store.load(room_id)
                transition = Transition()
                outcomes.append(hook(view, transition))
                committed += self.commit(view, transition)
        finally:
            await self._store.wake_committed(committed)
        return outcomes

    async def run[Outcome](self, view: RoomView, hook: Callable[[Transition], Outcome]) -> Outcome:
        """Apply the hook to the view it was given and commit what it changed; a hook that raises commits nothing."""
        transition = Transition()
        outcome = hook(transition)
        await self._store.wake_committed(self.commit(view, transition))
        return outcome

    def change_now(self, room_id: str, hook: Callable[[RoomView, Change], None]) -> None:
        """Apply the hook to the room's view now and commit its change before anything awaits; it cannot send, so no
        seat waits to be woken."""
        view = self._store.load(room_id)
        change = Change()
        hook(view, change)
        self.commit(view, change)

    def commit(self, view: RoomView, change: Change) -> list[Committed]:
        """Commit the change and start and stop its work, then each hook a transition follows with, leaving the seats
        of all of them to be woken."""
        event = change.broken_write_event if isinstance(change, Transition) else None
        committed = [Committed(self._store.commit(view, change), event)]
        self._start_and_stop_work(view, change)
        for follower in change.followers if isinstance(change, Transition) else ():
            followed = self._store.load(view.room_id)
            following = Transition()
            follower(followed, following)
            committed += self.commit(followed, following)
        return committed

    def _start_and_stop_work(self, view: RoomView, change: Change) -> None:
        for key in change.stopped:
            if key in self._works:
                self._worker.stop_work(key)
        for work in change.dispatched:
            if view.room_id is None:  # pragma: no cover - a hook dispatches work only in the room it was offered
                raise ValueError("A transition of no room dispatches no work")
            room = _WorkRoom(self, view.room_id)
            task = asyncio.create_task(self._work(work, room), name=work.key)
            self._works[work.key] = _RunningWork(view.room_id, task)

    async def _work(self, work: Work, room: _WorkRoom) -> None:
        try:
            await self._worker.run_work(work, self._live_seats, room)
        except Exception as error:
            # Only the type is logged, never what the work carried.
            logger.error("feature_work.failed", feature=self._worker.name, error_type=type(error).__name__)
        finally:
            del self._works[work.key]

    def in_own_turn(self, step: Coroutine[None, None, None], failed_event: str, **context: str) -> None:
        """Run a step of the feature, such as a transition whose seats are woken, in a turn of its own, so its caller
        never waits on a seat's write; a step that fails is logged under the event with the context, by the error's
        type alone, and the shutdown cancels a step that still runs."""
        task = asyncio.create_task(step)
        self._steps.add(task)
        task.add_done_callback(partial(self._step_ended, failed_event, context))

    def _step_ended(self, failed_event: str, context: Mapping[str, str], task: asyncio.Task[None]) -> None:
        self._steps.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            # Only the type is logged, never what the step carried.
            logger.error(failed_event, error_type=type(error).__name__, **context)

    def cancel(self, key: str) -> None:
        """Cancel the work of that key, which runs, for a worker whose work ends only so."""
        self._works[key].task.cancel()

    def room_ended(self, room_id: str) -> None:
        """Drop the sections of a room that has ended and stop its work."""
        self._store.end(room_id)
        for key, running in tuple(self._works.items()):
            if running.room_id == room_id:
                self._worker.stop_work(key)

    async def shut_down(self) -> None:
        """Stop the work and the steps and wait until they have ended."""
        for step in self._steps:
            step.cancel()
        await asyncio.gather(*self._steps, return_exceptions=True)
        while self._works:
            for key in tuple(self._works):
                self._worker.stop_work(key)
            await asyncio.wait([running.task for running in self._works.values()])


@dataclass(frozen=True)
class _WorkRoom:
    transitions: FeatureTransitions
    room_id: str

    async def view(self) -> RoomView:  # NOSONAR(S7503) the work reads the room between its awaits
        return self.transitions.view(self.room_id)

    async def apply[Outcome](self, hook: Callable[[RoomView, Transition], Outcome]) -> Outcome:
        return await self.transitions.apply(self.room_id, hook)
