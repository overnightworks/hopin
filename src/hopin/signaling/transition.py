"""A change of a call as one transition: load the room's view, apply a feature's hook to it, then commit the hook's
state and frames together and wake the seats it sent to."""

from dataclasses import dataclass, field
from functools import partial
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, cast

from hopin.signaling.frames import PeerId

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from starlette.requests import HTTPConnection

    from hopin.rooms.manager import RoomManager
    from hopin.signaling.features import TransitionFeature
    from hopin.signaling.frames import ForeignFrame, JsonObject
    from hopin.signaling.routing import RoomRouting


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
    sections: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def room_of(self, peer_id: PeerId) -> str | None:
        return self.room_id if peer_id in self.members else None

    def is_host(self, peer_id: PeerId) -> bool:
        """Whether the seat's account hosts the room."""
        return peer_id in self.hosts

    def section[State](self, section: Section[State]) -> State | None:
        # Only `Transition.put` writes a section, and it takes the section's own state type.
        return cast("State | None", self.sections.get(section.name))


@dataclass
class Transition:
    """What one hook changes: the state of its sections and the frames it sends, applied only by the commit."""

    puts: dict[str, object] = field(default_factory=dict)
    frames: list[tuple[PeerId, JsonObject]] = field(default_factory=list)

    def put[State](self, section: Section[State], state: State) -> None:
        self.puts[section.name] = state

    def send(self, peer_id: PeerId, frame: JsonObject) -> None:
        """Send as the call core sends: logged for a resume, held while the seat is parked or replaying."""
        self.frames.append((peer_id, frame))


class LiveSeats(Protocol):
    """The seats whose sockets this process holds."""

    def connection_of(self, peer_id: PeerId) -> HTTPConnection:
        """The connection that serves the seat now, carrying its sign-in; the seat's socket is held here."""


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
        return tuple(dict.fromkeys(peer_id for peer_id, frame in transition.frames if seats.log(peer_id, frame)))

    async def wake(self, peer_ids: tuple[PeerId, ...]) -> None:
        """Write the woken seats' logged frames to their sockets."""
        for peer_id in peer_ids:
            await self._routing.seats.deliver(peer_id)

    def end(self, room_id: str) -> None:
        """Drop the sections of a room that has ended."""
        self._sections.pop(room_id, None)


class TransitionHost:
    """Runs a feature written on transitions where the call core calls the hooks of `CallFeature`: each hook it is
    offered is one transition of the seat's room."""

    def __init__(self, feature: TransitionFeature, store: InMemoryCallStore) -> None:
        self._feature = feature
        self._store = store

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
        del name
        view = self._store.load(room_id)
        await self._run(view, partial(self._feature.seat_left, view, peer_id))

    async def _run[Outcome](self, view: RoomView, hook: Callable[[Transition], Outcome]) -> Outcome:
        """Apply the hook to the view it was given and commit what it changed; a hook that raises commits nothing."""
        transition = Transition()
        outcome = hook(transition)
        await self._store.wake(self._store.commit(view, transition))
        return outcome

    def seat_changed(self, peer_id: PeerId) -> None:
        """A feature on transitions hears a seat's leave as `seat_left`, in the transition that tells the room."""

    def chat_said(self, peer_id: PeerId, room_id: str, message: str) -> None:
        """No feature on transitions reads the chat."""

    def room_ended(self, room_id: str) -> None:
        self._store.end(room_id)

    async def shut_down(self) -> None:
        """A feature on transitions runs nothing in the background."""
