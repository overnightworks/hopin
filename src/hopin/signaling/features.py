"""The port through which the call's optional features plug into signaling.

Every feature runs on transitions (`TransitionFeature`; `transition.py` runs it). The handler holds the features as a
tuple and offers each the frames of the types it declares (`ForeignFrame`); a clash between features, or with the
core's types, is refused at start (`FeatureClashError`). It tells each feature:

- when a seat joined a room (`seat_joined`);
- when a seat parked, was resumed or left its room (`seat_changed`);
- when a seat has left its room for good (`seat_left`, with the room it left and the name the call showed);
- when a chat line was said, with the sender's seat and its room's view (`chat_said`);
- when a room ended (`room_ended`);

and shuts it down with the server. `seat_changed` and `chat_said` get a `Change`, which cannot send, and commit at once,
before the call core awaits anything. A seat that left its room is told to the room by `seat_left`, so the departed
seat's `seat_changed` commit has no seat to wake.

`create_app` takes the features as `call_features`: builders it calls on the app's own sign-in, scheduler, deadlines
and sign-in transaction, whose product is the feature, its router and, for a feature that serves a bearer token, the
protected resource the bearer gate admits. The features' `sign_in_scopes` join the sign-in's, their `frame_sources`
the Content-Security-Policy's `frame-src` (none keeps `'none'`), and `GET /api/call-features` (`web/call_features.py`)
names the browser modules of every wired feature. `main.py` passes the game and Skat in on lines marked `# game` and
`# skat`, which `scripts/remove_feature.py` deletes, and `create_app` builds the server without any when
`call_features` is left empty.
"""

from abc import ABC, abstractmethod
from collections import Counter
from typing import TYPE_CHECKING

from hopin.config.constants import SignalType
from hopin.signaling.transition import TransitionHost

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from hopin.signaling.frames import ForeignFrame, PeerId
    from hopin.signaling.transition import (
        Change,
        FeatureTransitions,
        InMemoryCallStore,
        LiveSeats,
        RoomView,
        Transition,
        Work,
        WorkRoom,
    )


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

    def chat_said(self, view: RoomView, peer_id: PeerId, message: str, change: Change) -> None:  # noqa: B027 - optional and a no-op for a feature without one
        """A seat's chat line is about to be relayed to the others in its room; called once per line the call core
        admits, its change committed before the core awaits anything, so the view's seats and the sender's name are
        those of the moment it was said. It sends nothing: the call core relays the line."""

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


class FeatureClashError(ValueError):
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
