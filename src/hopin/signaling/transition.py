"""A change of a call as one transition: load the room's view, apply a feature's hook to it, then commit the hook's
state and frames together and wake the seats it sent to. Work a transition dispatched runs beside the call, and its
results re-enter the room as transitions of their own."""

import asyncio
from dataclasses import dataclass, field, replace
from functools import partial
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, cast

import structlog

from hopin.signaling.frames import PeerId

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from starlette.requests import HTTPConnection

    from hopin.rooms.manager import RoomManager
    from hopin.signaling.features import TransitionFeature
    from hopin.signaling.frames import ForeignFrame, JsonObject
    from hopin.signaling.replay import Supersedes
    from hopin.signaling.routing import RoomRouting

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
    sections: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def room_of(self, peer_id: PeerId) -> str | None:
        return self.room_id if peer_id in self.members else None

    def name_of(self, peer_id: PeerId) -> str:
        """The name the call shows for a seat of the room, or for the seat whose leave the transition hears."""
        return self.names[peer_id]

    def is_host(self, peer_id: PeerId) -> bool:
        """Whether the seat's account hosts the room."""
        return peer_id in self.hosts

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
class Transition:
    """What one hook changes: the state of its sections, the frames it sends and the work it dispatches or stops,
    applied only by the commit."""

    puts: dict[str, object] = field(default_factory=dict)
    frames: list[Outbound] = field(default_factory=list)
    dispatched: list[Work] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    broken_write_event: str | None = None

    def put[State](self, section: Section[State], state: State) -> None:
        self.puts[section.name] = state

    def send(self, peer_id: PeerId, frame: JsonObject, supersedes: Supersedes | None = None) -> None:
        """Send as the call core sends: logged for a resume, held while the seat is parked or replaying. The commit
        drops the frames it supersedes from the seat's queue, so a seat a slow write holds back never gets an outdated
        one; a frame whose write has begun is written all the same."""
        self.frames.append(Outbound(peer_id, frame, supersedes))

    def dispatch(self, work: Work) -> None:
        """Run the work once the transition has committed."""
        self.dispatched.append(work)

    def stop(self, key: str) -> None:
        """Stop the feature's work of that key, if it still runs, once the transition has committed."""
        self.stopped.append(key)

    def log_broken_writes_as(self, event: str) -> None:
        """Log a seat's broken write of this transition's frames under the event, by the error's type alone, instead of
        raising it; every other seat has its frames either way."""
        self.broken_write_event = event


@dataclass(frozen=True)
class Committed:
    """A transition the store has committed, and the seats it logged frames for, which are still to be woken."""

    transition: Transition
    woken: tuple[PeerId, ...]


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

    def __init__(self, routing: RoomRouting, room_manager: RoomManager) -> None:
        self._routing = routing
        self._room_manager = room_manager
        self._sections: dict[str, dict[str, object]] = {}

    def load(self, room_id: str | None) -> RoomView:
        """The room's view now; an ended room or none is the view of no room."""
        room = None if room_id is None else self._room_manager.find_room(room_id)
        if room is None:
            return RoomView()
        account_of = self._routing.seats.account_of
        members = tuple(PeerId(participant_id) for participant_id in room.participant_ids)
        return RoomView(
            room_id=room.room_id,
            members=members,
            hosts=frozenset(peer_id for peer_id in members if room.is_hosted_by(account_of(peer_id))),
            names=MappingProxyType({peer_id: account_of(peer_id).name for peer_id in members}),
            sections=MappingProxyType(dict(self._sections.get(room.room_id, {}))),
        )

    def load_seat(self, peer_id: PeerId) -> RoomView:
        """The view of the room the seat sits in."""
        return self.load(self._routing.room_of(peer_id))

    def commit(self, view: RoomView, transition: Transition) -> tuple[PeerId, ...]:
        """Write the transition's sections into its room and log each frame for its seat, and name the seats to wake."""
        if transition.puts:
            if view.room_id is None:  # pragma: no cover - a hook keeps state only in the room it was offered
                raise ValueError("A transition of no room keeps no state")
            self._sections.setdefault(view.room_id, {}).update(transition.puts)
        seats = self._routing.seats
        return tuple(
            dict.fromkeys(
                outbound.peer_id
                for outbound in transition.frames
                if seats.log(outbound.peer_id, outbound.frame, outbound.supersedes)
            )
        )

    async def wake_committed(self, committed: Sequence[Committed]) -> None:
        """Wake the seats of transitions committed together once every one of them is logged, so a frame of one
        supersedes the other's before any is written. A broken write raises unless each transition names the event it
        is logged under."""
        woken = tuple(dict.fromkeys(peer_id for each in committed for peer_id in each.woken))
        broken_writes = await self.wake(woken)
        events = [each.transition.broken_write_event for each in committed]
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
                    await self._routing.seats.deliver(peer_id)
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


class TransitionHost:
    """Runs a feature written on transitions where the call core calls the hooks of `CallFeature`: each hook it is
    offered is one transition of the seat's room, and the work those transitions dispatch runs here, where the
    seats' sockets are held."""

    def __init__(self, feature: TransitionFeature, store: InMemoryCallStore, live_seats: LiveSeats) -> None:
        self._feature = feature
        self._store = store
        self._live_seats = live_seats
        self._works: dict[str, _RunningWork] = {}

    @property
    def name(self) -> str:
        return self._feature.name

    @property
    def browser_modules(self) -> tuple[str, ...]:
        return self._feature.browser_modules

    @property
    def signal_types(self) -> Mapping[str, str]:
        return self._feature.signal_types

    async def on_frame(self, peer_id: PeerId, frame: ForeignFrame) -> bool:
        view = self._store.load_seat(peer_id)
        return await self._run(view, partial(self._feature.on_frame, view, peer_id, frame))

    async def seat_joined(self, peer_id: PeerId) -> None:
        view = self._store.load_seat(peer_id)
        await self._run(view, partial(self._feature.seat_joined, view, peer_id))

    async def seat_left(self, peer_id: PeerId, *, room_id: str, name: str) -> None:
        """A feature on transitions hears a seat's leave in the transition that tells the room, `commit_seat_left`."""

    def commit_seat_left(self, peer_id: PeerId, *, room_id: str, name: str) -> Committed:
        """Apply the feature's `seat_left` to the room the seat left and commit it, leaving its seats to be woken
        together with the departure that tells the room."""
        loaded = self._store.load(room_id)
        view = replace(loaded, names=MappingProxyType({**loaded.names, peer_id: name}))
        transition = Transition()
        self._feature.seat_left(view, peer_id, transition)
        return self._commit(view, transition)

    def view(self, room_id: str) -> RoomView:
        return self._store.load(room_id)

    async def apply[Outcome](self, room_id: str, hook: Callable[[RoomView, Transition], Outcome]) -> Outcome:
        """Apply a work's result to its room's view now."""
        view = self._store.load(room_id)
        return await self._run(view, partial(hook, view))

    async def _run[Outcome](self, view: RoomView, hook: Callable[[Transition], Outcome]) -> Outcome:
        """Apply the hook to the view it was given and commit what it changed; a hook that raises commits nothing."""
        transition = Transition()
        outcome = hook(transition)
        await self._store.wake_committed([self._commit(view, transition)])
        return outcome

    def _commit(self, view: RoomView, transition: Transition) -> Committed:
        committed = Committed(transition, self._store.commit(view, transition))
        self._start_and_stop_work(view, transition)
        return committed

    def _start_and_stop_work(self, view: RoomView, transition: Transition) -> None:
        for key in transition.stopped:
            if key in self._works:
                self._feature.stop_work(key)
        for work in transition.dispatched:
            if view.room_id is None:  # pragma: no cover - a hook dispatches work only in the room it was offered
                raise ValueError("A transition of no room dispatches no work")
            room = _HostedWorkRoom(self, view.room_id)
            self._works[work.key] = _RunningWork(view.room_id, asyncio.create_task(self._work(work, room)))

    async def _work(self, work: Work, room: _HostedWorkRoom) -> None:
        try:
            await self._feature.run_work(work, self._live_seats, room)
        except Exception as error:
            # Only the type is logged, never what the work carried.
            logger.error("feature_work.failed", feature=self.name, error_type=type(error).__name__)
        finally:
            del self._works[work.key]

    def seat_changed(self, peer_id: PeerId) -> None:
        """A feature on transitions hears a seat's leave as `seat_left`, in the transition that tells the room."""

    def chat_said(self, peer_id: PeerId, room_id: str, message: str) -> None:
        """No feature on transitions reads the chat."""

    def room_ended(self, room_id: str) -> None:
        self._store.end(room_id)
        for key, running in tuple(self._works.items()):
            if running.room_id == room_id:
                self._feature.stop_work(key)
        self._feature.room_ended(room_id)

    async def shut_down(self) -> None:
        """Stop the feature's work, wait until it has ended, then shut the feature down."""
        while self._works:
            for key in tuple(self._works):
                self._feature.stop_work(key)
            await asyncio.wait([running.task for running in self._works.values()])
        await self._feature.shut_down()


@dataclass(frozen=True)
class _HostedWorkRoom:
    host: TransitionHost
    room_id: str

    async def view(self) -> RoomView:  # NOSONAR(S7503) the work reads the room between its awaits
        return self.host.view(self.room_id)

    async def apply[Outcome](self, hook: Callable[[RoomView, Transition], Outcome]) -> Outcome:
        return await self.host.apply(self.room_id, hook)
