from typing import TYPE_CHECKING, Protocol

from hopin.config.constants import SignalType

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

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
    """The one optional feature a call can carry, offered every frame the call core does not know."""

    @property
    def signal_types(self) -> Mapping[str, str]:
        """The frame types it adds to the browser's `SIGNAL_TYPE`, by name; none may be a core type."""

    @property
    def wire_exports(self) -> Mapping[str, Mapping[str, str]]:
        """The further named exports it adds to the browser's wire module; none may be a core export."""

    @property
    def browser_module(self) -> str:
        """The URL of its browser module, whose `install(core)` the page awaits before signaling connects."""

    async def on_frame(self, peer_id: PeerId, frame: ForeignFrame) -> bool:
        """Handle the frame and say so, or say it is none of the feature's."""

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
    """A feature names something the call core already owns; the call core refuses it at start."""

    def __init__(self, clashes: list[str]) -> None:
        super().__init__(f"The call's feature reuses the call core's {', '.join(clashes)}")


def build_feature(factory: FeatureFactory, rooms: CallRooms) -> CallFeature:
    feature = factory(rooms)
    clashes = sorted(
        {name for name in feature.signal_types if name in SignalType.__members__}
        | {value for value in feature.signal_types.values() if value in SignalType}
    )
    if clashes:
        raise FeatureClashError(clashes)
    return feature
