from abc import ABC, abstractmethod
from collections import Counter
from typing import TYPE_CHECKING, Protocol

from hopin.config.constants import SignalType
from hopin.signaling.transition import TransitionHost

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from starlette.requests import HTTPConnection

    from hopin.rooms.manager import RoomManager
    from hopin.signaling.frames import ForeignFrame, JsonObject, PeerId
    from hopin.signaling.routing import RoomRouting
    from hopin.signaling.transition import InMemoryCallStore, LiveSeats, RoomView, Transition


class CallRooms:
    """What a feature not yet on transitions may read of the calls and send into them; a new feature is a
    `TransitionFeature`."""

    def __init__(self, routing: RoomRouting, room_manager: RoomManager, live_seats: LiveSeats) -> None:
        self._routing = routing
        self._room_manager = room_manager
        self._live_seats = live_seats

    def room_of(self, peer_id: PeerId) -> str | None:
        return self._routing.room_of(peer_id)

    def members(self, room_id: str) -> list[str]:
        """The room's seats now; the room must exist."""
        return self._room_manager.get_room(room_id).participant_ids

    async def send(self, peer_id: str, frame: JsonObject) -> None:
        """Send as the call core sends: logged for a resume, held while the seat is parked or replaying."""
        await self._routing.seats.send(peer_id, frame)

    def name_of(self, peer_id: PeerId) -> str:
        """The name the call shows for the seat; the seat must exist."""
        return self._routing.seats.account_of(peer_id).name

    def seats_in(self, room_id: str, account_id: str) -> list[PeerId]:
        """The seats the account holds in the room now, parked ones included."""
        return [
            peer_id
            for peer_id, _ in self._routing.seats.held_by(account_id)
            if self._routing.room_of(peer_id) == room_id
        ]

    def has_free_place(self, room_id: str) -> bool:
        """Whether the room has a place left for another joined agent; the room must exist."""
        return self._room_manager.has_free_place_for_agent(room_id)

    def take_agent_place(self, room_id: str, agent_id: str) -> None:
        """Seat the joined agent among the room's people; the room must exist. Raises `RoomFullError` with no place
        left."""
        self._room_manager.add_agent(room_id, agent_id)

    def release_agent_place(self, room_id: str, agent_id: str) -> None:
        """Free the agent's place; a room that has ended holds none."""
        self._room_manager.remove_agent(room_id, agent_id)

    def is_parked(self, peer_id: PeerId) -> bool:
        """Whether the seat has lost its socket and waits for a resume; the seat must exist."""
        return self._routing.seats.seat(peer_id).park is not None

    def is_host(self, peer_id: PeerId) -> bool:
        """Whether the seat's account hosts the call it sits in; the seat must be in a room."""
        room_id = self.room_of(peer_id)
        return room_id is not None and self._room_manager.get_room(room_id).is_hosted_by(
            self._routing.seats.account_of(peer_id)
        )

    def connection_of(self, peer_id: PeerId) -> HTTPConnection:
        """The connection that serves the seat now, carrying its sign-in."""
        return self._live_seats.connection_of(peer_id)


class CallFeature(Protocol):
    """An optional feature of the call, offered each frame whose type it declares."""

    @property
    def name(self) -> str:
        """Its unique name."""

    @property
    def browser_modules(self) -> tuple[str, ...]:
        """The directories under `frontend/src/lib/features/` whose `install(core)` the page awaits before signaling
        connects; none for a feature the page does not know."""

    @property
    def signal_types(self) -> Mapping[str, str]:
        """The frame types it adds, by name; none may be a core type. Its browser module reads them from its own
        generated wire."""

    async def on_frame(self, peer_id: PeerId, frame: ForeignFrame) -> bool:
        """Handle a frame of a type the feature declared and say so, or say it is none of the feature's."""

    async def seat_joined(self, peer_id: PeerId) -> None:
        """A seat has just joined a room; a resumed seat has not."""

    async def seat_left(self, peer_id: PeerId, *, room_id: str, name: str) -> None:
        """A seat has left its room for good: its leave, its ended session, its grace run out or its unresumable
        seat released; called after the room has heard it left. A parked or resumed seat has not left, and a
        room's end calls `room_ended` instead."""

    def seat_changed(self, peer_id: PeerId) -> None:
        """A seat parked, a resume took it over, or it left its room; called at once after the change and before
        the call core awaits anything."""

    def chat_said(self, peer_id: PeerId, room_id: str, message: str) -> None:
        """A seat's chat line is about to be relayed to the others in its room; called once per line the call core
        admits, before the core awaits anything, so the room's seats and the sender's name are those of the moment
        it was said."""

    def room_ended(self, room_id: str) -> None:
        """The room is gone; called before the call core awaits anything or tells a seat."""

    async def shut_down(self) -> None:
        """Cancel the feature's work and wait until it has cleaned up."""


class TransitionFeature(ABC):
    """An optional feature of the call whose hooks are transitions: each reads the room's view and changes the
    feature's sections and sends frames only through its transition, which commits them together. A hook does store
    work only, never a call to another service or a socket."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Its unique name."""

    @property
    @abstractmethod
    def browser_modules(self) -> tuple[str, ...]:
        """As `CallFeature.browser_modules`."""

    @property
    @abstractmethod
    def signal_types(self) -> Mapping[str, str]:
        """As `CallFeature.signal_types`."""

    @abstractmethod
    def on_frame(self, view: RoomView, peer_id: PeerId, frame: ForeignFrame, transition: Transition) -> bool:
        """Handle a frame of a type the feature declared and say so, or say it is none of the feature's; an error it
        raises refuses the frame and commits nothing."""

    @abstractmethod
    def seat_joined(self, view: RoomView, peer_id: PeerId, transition: Transition) -> None:
        """As `CallFeature.seat_joined`."""

    @abstractmethod
    def seat_left(self, view: RoomView, peer_id: PeerId, transition: Transition) -> None:
        """As `CallFeature.seat_left`; the view is of the room the seat left. A room's end drops the feature's
        sections in it."""


type FeatureFactory = Callable[[CallRooms], CallFeature] | TransitionFeature
"""How the call core gets a feature: a factory it hands its rooms, or a feature on transitions, which needs none."""


class FeatureClashError(ValueError):
    """Features reuse a name the call core or another feature already owns; the call core refuses them at start."""

    def __init__(self, clashes: list[str]) -> None:
        super().__init__(f"The call's features reuse {', '.join(clashes)}")


def build_features(
    factories: Sequence[FeatureFactory], rooms: CallRooms, store: InMemoryCallStore
) -> tuple[CallFeature, ...]:
    features = tuple(
        TransitionHost(factory, store) if isinstance(factory, TransitionFeature) else factory(rooms)
        for factory in factories
    )
    clashes = _clashes(features)
    if clashes:
        raise FeatureClashError(clashes)
    return features


def _clashes(features: tuple[CallFeature, ...]) -> list[str]:
    core = {name for feature in features for name in feature.signal_types if name in SignalType.__members__} | {
        value for feature in features for value in feature.signal_types.values() if value in SignalType
    }
    between_features = {
        name
        for counted in (
            Counter(feature.name for feature in features),
            Counter(name for feature in features for name in feature.signal_types),
            Counter(value for feature in features for value in feature.signal_types.values()),
        )
        for name, uses in counted.items()
        if uses > 1
    }
    return sorted(core | between_features)
