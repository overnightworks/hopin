from collections import Counter
from typing import TYPE_CHECKING, Protocol

from hopin.config.constants import SignalType

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from starlette.requests import HTTPConnection

    from hopin.rooms.manager import RoomManager
    from hopin.signaling.frames import ForeignFrame, JsonObject, PeerId
    from hopin.signaling.routing import RoomRouting


class CallRooms:
    """What the call's feature may read of the calls and send into them."""

    def __init__(self, routing: RoomRouting, room_manager: RoomManager) -> None:
        self._routing = routing
        self._room_manager = room_manager

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

    def account_id_of(self, peer_id: PeerId) -> str:
        """The id of the account behind the seat; the seat must exist."""
        return self._routing.seats.account_of(peer_id).id

    def seats_in(self, room_id: str, account_id: str) -> list[PeerId]:
        """The seats the account holds in the room now, parked ones included."""
        return [
            peer_id
            for peer_id, _ in self._routing.seats.held_by(account_id)
            if self._routing.room_of(peer_id) == room_id
        ]

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
        return self._routing.seats.seat(peer_id).websocket


class CallFeature(Protocol):
    """An optional feature of the call, offered each frame whose type it declares."""

    @property
    def name(self) -> str:
        """Its name, which equals its directory under `frontend/src/lib/features/`, whose `install(core)` the page
        awaits before signaling connects."""

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

    def room_ended(self, room_id: str) -> None:
        """The room is gone; called before the call core awaits anything or tells a seat."""

    async def shut_down(self) -> None:
        """Cancel the feature's work and wait until it has cleaned up."""


type FeatureFactory = Callable[[CallRooms], CallFeature]


class FeatureClashError(ValueError):
    """Features reuse a name the call core or another feature already owns; the call core refuses them at start."""

    def __init__(self, clashes: list[str]) -> None:
        super().__init__(f"The call's features reuse {', '.join(clashes)}")


def build_features(factories: Sequence[FeatureFactory], rooms: CallRooms) -> tuple[CallFeature, ...]:
    features = tuple(factory(rooms) for factory in factories)
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
