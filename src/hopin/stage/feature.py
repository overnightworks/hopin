from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from hopin.errors import InvalidMessageError
from hopin.rooms.reference import room_reference
from hopin.signaling.features import TransitionFeature
from hopin.signaling.transition import Section
from hopin.stage.frames import AppEntry, AppsFrame, AttachedTableFrame, EmptyTableFrame, RefusedFrame
from hopin.stage.settings import (
    LinkedContext,
    StageApp,
    StageSettings,
    check_origins_differ,
    linked_context,
    named_context,
)
from hopin.stage.stage import OpenCalls, RoomState
from hopin.stage.start_link import create_start_link_router
from hopin.stage.vocabulary import FEATURE_NAME, SIGNAL_TYPES, StageRefusal, StageSignal

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from fastapi import APIRouter
    from webauth.config import WebAuthConfig
    from webauth.middleware import ProtectedResource
    from webauth.oidc import OidcRelyingParty
    from webauth.ports import SessionRecordStore, SignInTransaction

    from hopin.signaling.frames import ForeignFrame, PeerId
    from hopin.signaling.scheduler import Deadlines, Scheduler
    from hopin.signaling.transition import RoomView, Transition
    from hopin.web.page_build import PageBuild
    from hopin.web.request_language import RequestLanguage

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


STAGE = Section[RoomState](FEATURE_NAME)


class StageTransitions(TransitionFeature):
    """The table each call shows on its stage: any seat attaches one of a registered app's while none is shown, its
    opener or the host removes it, and anyone does once the opener has left for good. Every seat is sent the registered
    apps and the room's whole state. The first call to attach a table is the open call that shows it, which the start
    link sends a member to. `stage.attached` and `stage.detached` carry the room reference and the attachment number; a
    table id or link is never logged."""

    def __init__(self, apps: Sequence[StageApp]) -> None:
        self._apps = tuple(apps)
        self._open_calls = OpenCalls()
        self._handlers = {
            StageSignal.STAGE_ATTACH.value: self._on_attach,
            StageSignal.STAGE_DETACH.value: self._on_detach,
        }

    @property
    def name(self) -> str:
        return FEATURE_NAME

    @property
    def browser_modules(self) -> tuple[str, ...]:
        return (FEATURE_NAME,)

    @property
    def signal_types(self) -> Mapping[str, str]:
        return SIGNAL_TYPES

    def room_showing(self, app: str, context: str) -> str | None:
        """The open call that shows the table of that app and key, as this process knows it."""
        return self._open_calls.showing(app, context)

    def room_ended(self, room_id: str) -> None:
        self._open_calls.ended(room_id)

    def on_frame(self, view: RoomView, peer_id: PeerId, frame: ForeignFrame, transition: Transition) -> bool:
        handle = self._handlers.get(frame.signal_type)
        if handle is None:
            return False
        handle(view, peer_id, frame, transition)
        return True

    def seat_joined(self, view: RoomView, peer_id: PeerId, transition: Transition) -> None:
        """Tell a seat that just joined which apps it may show and what its room shows, also when that is nothing."""
        transition.send(peer_id, AppsFrame([AppEntry(app.name, app.title) for app in self._apps]).to_json())
        transition.send(peer_id, _table_frame(_state_of(view)).to_json())

    def seat_left(self, view: RoomView, peer_id: PeerId, transition: Transition) -> None:
        """A seat gone for good no longer opens the table it brought; tell the room's seats the table's state."""
        state = _state_of(view)
        if state.attachment is None:
            return
        state = state.without_opener(peer_id)
        transition.put(STAGE, state)
        _tell_room(view, state, transition)

    def _on_attach(self, view: RoomView, peer_id: PeerId, frame: ForeignFrame, transition: Transition) -> None:
        requested = self._requested_table(frame)
        room_id = view.room_of(peer_id)
        if room_id is None:
            return
        if requested is None:
            transition.send(peer_id, RefusedFrame(StageRefusal.BAD_LINK).to_json())
            return
        attached = _state_of(view).attached(
            app=requested.app.name, table=requested.context, source=requested.source, opener=peer_id
        )
        if attached is None:
            transition.send(peer_id, RefusedFrame(StageRefusal.BUSY).to_json())
            return
        logger.info("stage.attached", room_ref=room_reference(room_id), attachment=attached.revision)
        transition.put(STAGE, attached)
        self._open_calls.attached(requested.app.name, requested.context, room_id)
        _tell_room(view, attached, transition)

    def _requested_table(self, frame: ForeignFrame) -> LinkedContext | None:
        """The table an attach names, by a pasted link or by an app and a key, or nothing when it names none of a
        registered app's."""
        link = frame.data.get("link")
        if isinstance(link, str):
            return linked_context(self._apps, link)
        app = frame.data.get("app")
        context = frame.data.get("context")
        if isinstance(app, str) and isinstance(context, str):
            return named_context(self._apps, app, context)
        raise InvalidMessageError("An attach needs the table's link, or its app and key")

    def _on_detach(self, view: RoomView, peer_id: PeerId, frame: ForeignFrame, transition: Transition) -> None:
        attachment = frame.data.get("attachment")
        if not isinstance(attachment, int) or isinstance(attachment, bool):
            raise InvalidMessageError("A detach needs the attachment's number")
        room_id = view.room_of(peer_id)
        state = _state_of(view)
        held = state.attachment
        if room_id is None or held is None or held.number != attachment:
            return
        if held.opener not in (None, peer_id) and not view.is_host(peer_id):
            transition.send(peer_id, RefusedFrame(StageRefusal.NOT_OPENER_OR_HOST).to_json())
            return
        logger.info("stage.detached", room_ref=room_reference(room_id), attachment=attachment)
        detached = state.detached()
        transition.put(STAGE, detached)
        self._open_calls.detached(held.app, held.table, room_id)
        _tell_room(view, detached, transition)


def _state_of(view: RoomView) -> RoomState:
    return view.section(STAGE) or RoomState()


def _table_frame(state: RoomState) -> AttachedTableFrame | EmptyTableFrame:
    held = state.attachment
    if held is None:
        return EmptyTableFrame(state.revision, None, None, None, None, None)
    return AttachedTableFrame(state.revision, held.number, held.app, held.table, held.source, held.opener)


def _tell_room(view: RoomView, state: RoomState, transition: Transition) -> None:
    for member in view.members:
        transition.send(member, _table_frame(state).to_json())


@dataclass(frozen=True)
class BuiltStageFeature:
    """What the stage adds to the app: its call feature and the route of its start link, and no bearer resource."""

    feature: TransitionFeature
    router: APIRouter
    bearer_resource: ProtectedResource | None = None


@dataclass(frozen=True)
class StageFeature:
    """The stage, built inside `create_app`: the call may frame the tables of the registered apps."""

    apps: tuple[StageApp, ...]

    @property
    def sign_in_scopes(self) -> tuple[str, ...]:
        """None: each frame signs in at its app itself."""
        return ()

    @property
    def frame_sources(self) -> tuple[str, ...]:
        """The origin of every registered app, which are the origins the page may frame."""
        return tuple(app.origin for app in self.apps)

    def __call__(
        self,
        *,
        relying_party: OidcRelyingParty,
        sessions: SessionRecordStore,
        auth_config: WebAuthConfig,
        scheduler: Scheduler,
        deadlines: Deadlines,
        transaction: SignInTransaction,
        request_language: RequestLanguage,
        page_build: PageBuild,
    ) -> BuiltStageFeature:
        del relying_party, sessions, auth_config, scheduler, deadlines, transaction
        transitions = StageTransitions(self.apps)
        router = create_start_link_router(self.apps, transitions.room_showing, page_build, request_language)
        return BuiltStageFeature(transitions, router)


def configured_stage_feature(public_origin: str) -> tuple[StageFeature, ...]:
    """The stage as the environment configures it: one feature for a registry with apps, none for an empty one."""
    apps = StageSettings().apps
    check_origins_differ(apps, public_origin)
    return (StageFeature(apps),) if apps else ()
