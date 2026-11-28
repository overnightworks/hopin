"""The port through which the call's optional features plug into signaling.

Every feature runs on transitions (`TransitionFeature`; `transition.py` runs it). The handler holds the features as a
tuple and offers each the frames of the types it declares (`ForeignFrame`); a clash between features, or with the
core's types, is refused at start (`FeatureClashError`). The hooks tell a feature what happens to its seats and rooms;
each states its own contract.

`create_app` takes the features as `call_features`: builders it calls on the app's own sign-in, scheduler, deadlines
and sign-in transaction, whose product is the feature, its router and, for a feature that serves a bearer token, the
protected resource the bearer gate admits. The features' `sign_in_scopes` join the sign-in's, their `frame_sources`
the Content-Security-Policy's `frame-src` (none keeps `'none'`), and `GET /api/call-features` (`web/call_features.py`)
names the browser modules of every wired feature. `main.py` passes the game and Skat in on lines marked `# game` and
`# stage`, which `scripts/remove_feature.py` deletes, and `create_app` builds the server without any when
`call_features` is left empty.
"""

from abc import ABC, abstractmethod
from collections import Counter
from functools import partial
from typing import TYPE_CHECKING

from hopin.config.constants import SignalType
from hopin.config.secret_files import StartRefusedError
from hopin.signaling.transition import Change, Committed, FeatureTransitions, Transition

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from hopin.signaling.frames import ForeignFrame, PeerId
    from hopin.signaling.transition import InMemoryCallStore, LiveSeats, RoomView, Work, WorkRoom


class TransitionFeature(ABC):
    """An optional feature of the call, offered each frame whose type it declares. Its hooks are transitions: each
    reads the room's view and changes the feature's sections and sends frames only through its transition, which
    commits them together. A hook does store work only, never a call to another service or a socket: such a call is
    work, which the transition dispatches."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Its unique name."""

    @property
    @abstractmethod
    def browser_modules(self) -> tuple[str, ...]:
        """The directories under `frontend/src/lib/features/` whose `install(core)` the page awaits before signaling
        connects; none for a feature the page does not know."""

    @property
    @abstractmethod
    def signal_types(self) -> Mapping[str, str]:
        """The frame types it adds, by name; none may be a core type. Its browser module reads them from its own
        generated wire."""

    def attach(self, transitions: FeatureTransitions) -> None:  # noqa: B027 - optional and a no-op for a feature without one
        """Take the transitions the call core runs the feature on, for the changes the feature starts itself; called
        once, before any hook."""

    @abstractmethod
    def on_frame(self, view: RoomView, peer_id: PeerId, frame: ForeignFrame, transition: Transition) -> bool:
        """Handle a frame of a type the feature declared and say so, or say it is none of the feature's; an error it
        raises refuses the frame and commits nothing."""

    @abstractmethod
    def seat_joined(self, view: RoomView, peer_id: PeerId, transition: Transition) -> None:
        """A seat has just joined a room; a resumed seat has not."""

    @abstractmethod
    def seat_left(self, view: RoomView, peer_id: PeerId, transition: Transition) -> None:
        """A seat has left its room for good: its leave, its ended session, its grace run out or its unresumable seat
        released; the view is of the room the seat left, and the transition commits with the departure that tells the
        room. A parked or resumed seat has not left, and a room's end drops the feature's sections in it instead."""

    def seat_changed(self, view: RoomView, peer_id: PeerId, change: Change) -> None:  # noqa: B027 - optional and a no-op for a feature without one
        """A seat parked, a resume took it over, or it left its room; the view is of the seat's room now, none for a
        seat that left, and the change commits at once, before the call core awaits anything. It sends nothing: a
        leave is told to the room by `seat_left`."""

    def room_ended(self, room_id: str) -> None:  # noqa: B027 - optional and a no-op for a feature without one
        """Release what the feature keeps for the room beside its sections; its sections are dropped and its work
        stopped already."""

    async def run_work(self, work: Work, live_seats: LiveSeats, room: WorkRoom) -> None:  # noqa: B027 - optional and a no-op for a feature without one
        """Run a work the feature dispatched, on the process holding its seat's socket: the only place a connection
        is reached, through `live_seats`. Its results re-enter the room through `room`, each as a transition."""

    def stop_work(self, key: str) -> None:  # noqa: B027 - optional and a no-op for a feature without one
        """Make the running work of that key end soon, its room's end, a transition or the shutdown asks it to; called
        only while the work runs."""

    async def shut_down(self) -> None:  # noqa: B027 - optional and a no-op for a feature without one
        """Release what the feature holds, once its work and its steps have ended."""


class TransitionHost:
    """Runs a feature where the call core calls its hooks: each hook it is offered is one transition of the seat's
    room, and the work those transitions dispatch runs here, where the seats' sockets are held."""

    def __init__(self, feature: TransitionFeature, store: InMemoryCallStore, live_seats: LiveSeats) -> None:
        self._feature = feature
        self._transitions = FeatureTransitions(feature, store, live_seats)
        feature.attach(self._transitions)

    @property
    def feature(self) -> TransitionFeature:
        return self._feature

    @property
    def browser_modules(self) -> tuple[str, ...]:
        return self._feature.browser_modules

    @property
    def signal_types(self) -> Mapping[str, str]:
        return self._feature.signal_types

    async def on_frame(self, peer_id: PeerId, frame: ForeignFrame) -> bool:
        view = await self._transitions.seat_view(peer_id)
        return await self._transitions.run(view, partial(self._feature.on_frame, view, peer_id, frame))

    async def seat_joined(self, peer_id: PeerId) -> None:
        view = await self._transitions.seat_view(peer_id)
        await self._transitions.run(view, partial(self._feature.seat_joined, view, peer_id))

    async def commit_seat_left(self, peer_id: PeerId, *, room_id: str, name: str) -> list[Committed]:
        """Apply the feature's `seat_left` to the room the seat left and commit it, leaving its seats to be woken
        together with the departure that tells the room."""
        view = (await self._transitions.view(room_id)).naming(peer_id, name)
        transition = Transition()
        self._feature.seat_left(view, peer_id, transition)
        return await self._transitions.commit(view, transition)

    async def seat_changed(self, peer_id: PeerId) -> None:
        """Apply the feature's `seat_changed` to the view of the seat's room now and commit it at once; it cannot send,
        so no seat waits to be woken."""
        view = await self._transitions.seat_view(peer_id)
        change = Change()
        self._feature.seat_changed(view, peer_id, change)
        await self._transitions.commit(view, change)

    def room_ended(self, room_id: str) -> None:
        self._transitions.room_ended(room_id)
        self._feature.room_ended(room_id)

    async def shut_down(self) -> None:
        """Stop the feature's work, wait until it has ended, then shut the feature down."""
        await self._transitions.shut_down()
        await self._feature.shut_down()


class FeatureClashError(StartRefusedError):
    """Features reuse a name the call core or another feature already owns; the call core refuses them at start."""

    def __init__(self, clashes: list[str]) -> None:
        super().__init__(f"The call's features reuse {', '.join(clashes)}")


def build_features(
    features: Sequence[TransitionFeature], store: InMemoryCallStore, live_seats: LiveSeats
) -> tuple[TransitionHost, ...]:
    clashes = _clashes(features)
    if clashes:
        raise FeatureClashError(clashes)
    return tuple(TransitionHost(feature, store, live_seats) for feature in features)


def _clashes(features: Sequence[TransitionFeature]) -> list[str]:
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
