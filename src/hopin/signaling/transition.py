"""A change of a call as one transition: load the room's view, apply a feature's hook to it, then commit the hook's
state and frames together and wake the seats it sent to. The call core's own actions (a seat opening, a join, a lock, a
leave, a park, a resume, an end) are transitions too, made of edits to rooms and seats beside the frames they send. The
features and the call core commit through one store (`call_store.py`: the calls live in hopin's Redis, and one script
commits each transition), and the commit is the only writer of rooms, seats and seat logs. Work a transition
dispatched runs beside the call, and its results re-enter the room as transitions of their own.

Each hook reads the room's view (`RoomView`: its seats and their accounts and names, its hosts, its parked seats,
whether an agent place is free and the feature's own `Section` of state in that room), puts state, takes or frees agent
places and sends frames only through its `Transition`, and does no other work. The store commits both in one script, and
a hook that raises commits neither. A room's transitions run one at a time within a process, and a hook runs once: a
commit that meets another process's change to what the hook read fails loud. Only then are the seats it sent to woken,
each writing its logged frames in the order of their seq.

- A transition may follow itself with another hook, which reads the room as the first one left it; the seats of both
  are woken together.
- A seat's departure is told in one commit with each feature's `seat_left`, before any seat is woken, so the room
  never hears an outdated frame after the leave.
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
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, cast

import structlog

from hopin.config.constants import RESUME_WINDOW_SECONDS
from hopin.signaling.call_store import (
    Commit,
    Edit,
    HostGraceCleared,
    HostGraceStarted,
    Outbound,
    ParticipantRemoved,
    RoomEnded,
    SeatForgotten,
    SeatParked,
    SeatTakenOver,
)
from hopin.signaling.frames import PeerId
from hopin.signaling.own_turns import OwnTurns
from hopin.signaling.scheduler import DeadlineKind
from hopin.signaling.seats import Seats

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
    from contextlib import AbstractAsyncContextManager

    from starlette.requests import HTTPConnection

    from hopin.signaling.call_store import CallStore
    from hopin.signaling.frames import JsonObject
    from hopin.signaling.replay import Supersedes
    from hopin.signaling.scheduler import Deadlines
    from hopin.signaling.seats import SeatSocket

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
    elsewhere: frozenset[PeerId] = frozenset()
    """The seats another process generation holds, whose work that process runs."""
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


@dataclass
class Change:
    """What a hook that sends nothing changes: the state of its sections, the agent places it takes or frees and the
    work it dispatches or stops, applied only by the commit."""

    puts: dict[str, object] = field(default_factory=dict)
    acting: PeerId | None = None
    dispatched: list[Work] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    seated_agents: list[str] = field(default_factory=list)
    released_agents: list[str] = field(default_factory=list)
    in_rooms: list[tuple[str, Callable[[RoomView, Change], None]]] = field(default_factory=list)

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

    def in_room(self, room_id: str, hook: Callable[[RoomView, Change], None]) -> None:
        """Apply the hook to that room's view as a change of its own, committed right after this one, for a room this
        change's view is not of, such as the room a seat has left."""
        self.in_rooms.append((room_id, hook))

    def outbound(self) -> Sequence[Outbound]:
        """The frames the commit logs: none for a change that cannot send."""
        return ()


@dataclass
class Transition(Change):
    """What one hook changes and the frames it sends, applied only by the commit; a hook it follows with runs on the
    room's view once it has committed."""

    frames: list[Outbound] = field(default_factory=list)
    followers: list[Callable[[RoomView, Transition], None]] = field(default_factory=list)
    log_broken_write: Callable[[BaseException], None] | None = None

    def send(self, peer_id: PeerId, frame: JsonObject, supersedes: Supersedes | None = None) -> None:
        """Send as the call core sends: logged for a resume, held while the seat is parked or replaying. The commit
        drops the frames it supersedes from the seat's queue, so a seat a slow write holds back never gets an outdated
        one; a frame whose write has begun is written all the same."""
        self.frames.append(Outbound(peer_id, frame, supersedes))

    def follow(self, hook: Callable[[RoomView, Transition], None]) -> None:
        """Apply the hook to the room's view as a transition of its own once this one has committed, so it reads what
        this one changed; the seats of both are woken together."""
        self.followers.append(hook)

    def log_broken_writes_with(self, log: Callable[[BaseException], None]) -> None:
        """Log a seat's broken write of this transition's frames with `log`, which writes the error's type alone,
        instead of raising it; every other seat has its frames either way."""
        self.log_broken_write = log

    def outbound(self) -> Sequence[Outbound]:
        return self.frames


@dataclass
class CoreTransition(Transition):
    """A transition of the call core: the edits it makes to rooms and seats, applied in order by the commit, beside the
    frames it sends. An edit that refuses, such as a join of a locked room, leaves the edits after it unapplied."""

    edits: list[Edit] = field(default_factory=list)
    sockets: dict[PeerId, SeatSocket] = field(default_factory=dict)

    def edit(self, edit: Edit) -> None:
        self.edits.append(edit)

    def attach_socket(self, peer_id: PeerId, socket: SeatSocket) -> None:
        """Make the socket the one that serves the seat, once the edits have committed."""
        self.sockets[peer_id] = socket


@dataclass(frozen=True)
class Committed:
    """A change the store has committed: the seats it logged frames for, which are still to be woken, and the log a
    broken write of them is written with, none to raise it."""

    woken: tuple[PeerId, ...]
    log_broken_write: Callable[[BaseException], None] | None = None


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


class Calls:
    """This process's calls as the transitions see them: the store's records, the sockets this process holds for its
    seats, the deadlines it times and the sections the features keep in each room. A commit writes the store first; the
    process's own effects (sockets, deadlines, sections, the callbacks that tell the features) follow it."""

    def __init__(
        self,
        store: CallStore,
        deadlines: Deadlines,
        on_room_ended: Callable[[str], None],
        on_seat_changed: Callable[[PeerId], Awaitable[None]],
    ) -> None:
        self._store = store
        self._deadlines = deadlines
        self._on_room_ended = on_room_ended
        self._on_seat_changed = on_seat_changed
        self._seats = Seats(store)
        self._sections: dict[str, dict[str, object]] = {}
        self._unwritten_seqs: dict[PeerId, tuple[int, asyncio.Task[object] | None]] = {}

    @property
    def seats(self) -> Seats:
        return self._seats

    @property
    def store(self) -> CallStore:
        return self._store

    def action(self, room_id: str | None) -> AbstractAsyncContextManager[dict[str, object]]:
        """Run the decisions and commits of one room, one at a time within this process."""
        return self._store.action(room_id)

    async def load(self, room_id: str | None) -> RoomView:
        """The room's view now; an ended room or none is the view of no room."""
        room = None if room_id is None else await self._store.find_room(room_id)
        if room is None:
            return RoomView()
        members = tuple(PeerId(peer_id) for peer_id in room.participant_ids)
        records = dict(
            zip(members, await asyncio.gather(*(self._store.record_of(peer_id) for peer_id in members)), strict=True)
        )
        return RoomView(
            room_id=room.room_id,
            members=members,
            hosts=frozenset(peer_id for peer_id in members if room.is_hosted_by(records[peer_id].account)),
            names=MappingProxyType({peer_id: records[peer_id].account.name for peer_id in members}),
            accounts=MappingProxyType({peer_id: records[peer_id].account.id for peer_id in members}),
            parked=frozenset(peer_id for peer_id in members if records[peer_id].park is not None),
            elsewhere=frozenset(
                peer_id for peer_id in members if records[peer_id].generation != self._store.generation
            ),
            has_free_place=room.has_free_place_for_agent(self._store.place_limit),
            sections=MappingProxyType(dict(self._sections.get(room.room_id, {}))),
        )

    async def room_of(self, peer_id: PeerId) -> str | None:
        return await self._store.room_of(peer_id)

    async def load_seat(self, peer_id: PeerId) -> RoomView:
        """The view of the room the seat sits in."""
        return await self.load(await self._store.room_of(peer_id))

    def receive(self, peer_id: PeerId, seq: int) -> None:
        """Note the seq of the browser's frame about to be applied. Only a commit made by the task that handles the
        frame writes it, after the frame's edits applied, so a frame is never counted received without its effect and
        another seat's commit never takes it. A frame that changes nothing is settled by `settle_received`."""
        self._unwritten_seqs[peer_id] = (seq, asyncio.current_task())

    async def settle_received(self, peer_id: PeerId) -> None:
        """Commit the seq of a frame that no commit has written, which changed nothing."""
        if peer_id in self._unwritten_seqs:
            await self.commit_edits(CoreTransition())

    async def commit(self, view: RoomView, change: Change) -> tuple[PeerId, ...]:
        """Write the change's edits, agent places and sections and log each frame it sends for its seat, and name the
        seats to wake. The seq of the browser frame this task handles is written once the edits applied, so a refused
        edit leaves it to the commit that tells the browser of the refusal."""
        edits = tuple(change.edits) if isinstance(change, CoreTransition) else ()
        if view.room_id is None and (
            change.puts or change.seated_agents
        ):  # pragma: no cover - a hook keeps state in its room
            raise ValueError("A transition of no room keeps no state")
        forgotten = {edit.peer_id for edit in edits if isinstance(edit, SeatForgotten)}
        resumed_from_park = {
            edit.peer_id: (await self._store.record_of(edit.peer_id)).park is not None
            for edit in edits
            if isinstance(edit, SeatTakenOver)
        }
        received = self._received_by_this_task()
        woken = await self._store.commit(
            Commit(
                edits=edits,
                frames=tuple(change.outbound()),
                room_id=view.room_id,
                places_taken=tuple(change.seated_agents),
                places_released=tuple(change.released_agents),
                acting=change.acting,
                received={peer_id: seq for peer_id, seq in received.items() if peer_id not in forgotten},
            )
        )
        for peer_id in received:
            self._unwritten_seqs.pop(peer_id, None)
        if view.room_id is not None and change.puts:
            self._sections.setdefault(view.room_id, {}).update(change.puts)
        for peer_id, socket in (change.sockets if isinstance(change, CoreTransition) else {}).items():
            socket.generation = self._store.generation
            self._seats.attach(peer_id, socket)
        for edit in edits:
            await self._follow(edit, resumed_from_park)
        return woken

    def _received_by_this_task(self) -> dict[PeerId, int]:
        handling = asyncio.current_task()
        return {peer_id: seq for peer_id, (seq, task) in self._unwritten_seqs.items() if task is handling}

    async def commit_edits(self, transition: CoreTransition) -> None:
        """Commit a transition of the call core that sends nothing, so no seat waits to be woken."""
        await self.commit(RoomView(), transition)

    async def commit_core(self, transition: CoreTransition) -> Committed:
        """Commit a transition of the call core, leaving the seats it sent to to be woken."""
        return Committed(await self.commit(RoomView(), transition), transition.log_broken_write)

    async def _follow(self, edit: Edit, resumed_from_park: Mapping[PeerId, bool]) -> None:
        """What this process does once the store holds the edit: its deadlines, and the features' callbacks."""
        match edit:
            case SeatForgotten(peer_id):
                self._unwritten_seqs.pop(peer_id, None)
                self._seats.detach(peer_id)
                self._deadlines.cancel(DeadlineKind.RESUME_WINDOW, peer_id)
            case SeatParked(peer_id, _):
                self._deadlines.set(DeadlineKind.RESUME_WINDOW, peer_id, RESUME_WINDOW_SECONDS)
                await self._on_seat_changed(peer_id)
            case SeatTakenOver(peer_id, _):
                self._deadlines.cancel(DeadlineKind.RESUME_WINDOW, peer_id)
                if resumed_from_park[peer_id]:
                    await self._on_seat_changed(peer_id)
            case ParticipantRemoved(_, peer_id):
                await self._on_seat_changed(peer_id)
            case RoomEnded(room_id, _):
                self._deadlines.cancel(DeadlineKind.HOST_GRACE, room_id)
                self._on_room_ended(room_id)
            case HostGraceStarted(room_id, _, deadline):
                self._deadlines.set(DeadlineKind.HOST_GRACE, room_id, deadline - self._deadlines.now())
            case HostGraceCleared(room_id):
                self._deadlines.cancel(DeadlineKind.HOST_GRACE, room_id)
            case _:
                pass

    async def wake_committed(self, committed: Sequence[Committed]) -> None:
        """Wake the seats of transitions committed together once every one of them is logged, so a frame of one
        supersedes the other's before any is written. A broken write raises unless each transition names the log it
        is written with."""
        woken = tuple(dict.fromkeys(peer_id for each in committed for peer_id in each.woken))
        broken_writes = await self.wake(woken)
        logs = [each.log_broken_write for each in committed]
        if not broken_writes:
            return
        first = logs[0]
        if first is None or None in logs:
            raise broken_writes[0]
        for error in broken_writes:
            first(error)

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

    def __init__(self, worker: Worker, store: Calls, live_seats: LiveSeats) -> None:
        self._worker = worker
        self._store = store
        self._live_seats = live_seats
        self._works: dict[str, _RunningWork] = {}
        self._steps = OwnTurns()

    async def view(self, room_id: str | None) -> RoomView:
        """The room's view now; an ended room or none is the view of no room."""
        return await self._store.load(room_id)

    async def seat_view(self, peer_id: PeerId) -> RoomView:
        """The view of the room the seat sits in."""
        return await self._store.load_seat(peer_id)

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
                async with self._store.action(room_id):
                    view = await self._store.load(room_id)
                    transition = Transition()
                    outcomes.append(hook(view, transition))
                    committed += await self.commit(view, transition)
        finally:
            await self._store.wake_committed(committed)
        return outcomes

    async def in_seat_room[Outcome](
        self, peer_id: PeerId, step: Callable[[RoomView], Awaitable[tuple[Outcome, list[Committed]]]]
    ) -> Outcome:
        """Run the step on the view of the seat's room, with the room's transitions held to this one; the seats it
        committed for are woken once the room is released."""
        room_id = await self._store.room_of(peer_id)
        committed: list[Committed] = []
        try:
            async with self._store.action(room_id):
                outcome, committed = await step(await self._store.load(room_id))
        finally:
            await self._store.wake_committed(committed)
        return outcome

    async def run[Outcome](
        self, view: RoomView, hook: Callable[[Transition], Outcome], acting: PeerId | None = None
    ) -> tuple[Outcome, list[Committed]]:
        """Apply the hook to the view it was given and commit what it changed, for the seat `acting` it answers; a hook
        that raises commits nothing."""
        transition = Transition(acting=acting)
        outcome = hook(transition)
        return outcome, await self.commit(view, transition)

    async def change_now(self, room_id: str, hook: Callable[[RoomView, Change], None]) -> None:
        """Apply the hook to the room's view now and commit its change before anything suspends; it cannot send, so no
        seat waits to be woken."""
        async with self._store.action(room_id):
            view = await self._store.load(room_id)
            change = Change()
            hook(view, change)
            await self.commit(view, change)

    async def commit(self, view: RoomView, change: Change) -> list[Committed]:
        """Commit the change and start and stop its work, then each hook a transition follows with, leaving the seats
        of all of them to be woken."""
        log = change.log_broken_write if isinstance(change, Transition) else None
        committed = [Committed(await self._store.commit(view, change), log)]
        self._start_and_stop_work(view, change)
        for room_id, hook in change.in_rooms:
            await self.change_now(room_id, hook)
        for follower in change.followers if isinstance(change, Transition) else ():
            followed = await self._store.load(view.room_id)
            following = Transition()
            follower(followed, following)
            committed += await self.commit(followed, following)
        return committed

    def _start_and_stop_work(self, view: RoomView, change: Change) -> None:
        for key in change.stopped:
            if key in self._works:
                self._worker.stop_work(key)
        for work in change.dispatched:
            if work.seat in view.elsewhere:
                continue
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

    def in_own_turn(self, step: Coroutine[None, None, None], log_failure: Callable[[BaseException], None]) -> None:
        """Run a step of the feature, such as a transition whose seats are woken, in a turn of its own, so its caller
        never waits on a seat's write; a step that fails is written with `log_failure`, which names the error's type
        alone, and the shutdown cancels a step that still runs."""
        self._steps.start(step, log_failure)

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
        self._steps.cancel()
        await self._steps.wait()
        while self._works:
            for key in tuple(self._works):
                self._worker.stop_work(key)
            await asyncio.wait([running.task for running in self._works.values()])


@dataclass(frozen=True)
class _WorkRoom:
    transitions: FeatureTransitions
    room_id: str

    async def view(self) -> RoomView:
        return await self.transitions.view(self.room_id)

    async def apply[Outcome](self, hook: Callable[[RoomView, Transition], Outcome]) -> Outcome:
        return await self.transitions.apply(self.room_id, hook)
