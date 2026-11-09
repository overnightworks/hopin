from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter

from hopin.errors import InvalidMessageError
from hopin.signaling.frames import TYPE_KEY, JsonObject, PeerId
from hopin.skat.settings import SkatSettings, check_origin_differs, table_source
from hopin.skat.stage import Stages
from hopin.skat.vocabulary import FEATURE_NAME, SIGNAL_TYPES, SkatRefusal, SkatSignal

if TYPE_CHECKING:
    from collections.abc import Mapping

    from webauth.config import WebAuthConfig
    from webauth.middleware import ProtectedResource
    from webauth.oidc import OidcRelyingParty
    from webauth.ports import SessionRecordStore, SignInTransaction

    from hopin.signaling.features import CallRooms, FeatureFactory
    from hopin.signaling.frames import ForeignFrame
    from hopin.signaling.scheduler import Scheduler

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


def _refusal_frame(refusal: SkatRefusal) -> JsonObject:
    return {TYPE_KEY: SkatSignal.SKAT_REFUSED.value, "reason": refusal.value}


class Skat:
    """The table each call shows on its stage: any seat attaches one while none is shown, its opener or the host
    removes it, and anyone does once the opener has left for good. Every seat is sent the room's whole state."""

    def __init__(self, rooms: CallRooms, origin: str) -> None:
        self._rooms = rooms
        self._origin = origin
        self._stages = Stages()
        self._handlers = {SkatSignal.SKAT_ATTACH.value: self._on_attach, SkatSignal.SKAT_DETACH.value: self._on_detach}

    @property
    def name(self) -> str:
        return FEATURE_NAME

    @property
    def signal_types(self) -> Mapping[str, str]:
        return SIGNAL_TYPES

    async def on_frame(self, peer_id: PeerId, frame: ForeignFrame) -> bool:
        handle = self._handlers.get(frame.signal_type)
        if handle is None:
            return False
        await handle(peer_id, frame)
        return True

    async def seat_joined(self, peer_id: PeerId) -> None:
        """Tell a seat that just joined what its room shows, also when that is nothing."""
        room_id = self._rooms.room_of(peer_id)
        if room_id is None:  # pragma: no cover - a seat is in its room when it is announced
            return
        await self._rooms.send(peer_id, self._table_frame(room_id))

    def chat_said(self, peer_id: PeerId, room_id: str, message: str) -> None:
        """Skat reads no chat."""

    def seat_changed(self, peer_id: PeerId) -> None:
        """A seat that left its room no longer opens the table it brought; `seat_left` tells the room."""
        if self._rooms.room_of(peer_id) is None:
            self._stages.forget_opener(peer_id)

    async def seat_left(self, peer_id: PeerId, *, room_id: str, name: str) -> None:
        """Tell the room's seats the table's state once a seat has gone, which shows the opener gone when it was one."""
        del peer_id, name
        if self._stages.state_of(room_id).attachment is not None:
            await self._tell_room(room_id)

    def room_ended(self, room_id: str) -> None:
        self._stages.end(room_id)

    async def shut_down(self) -> None:
        """Nothing runs in the background."""

    async def _on_attach(self, peer_id: PeerId, frame: ForeignFrame) -> None:
        link = frame.data.get("link")
        if not isinstance(link, str):
            raise InvalidMessageError("An attach needs the table's link")
        room_id = self._rooms.room_of(peer_id)
        if room_id is None:
            return
        source = table_source(self._origin, link)
        if source is None:
            await self._rooms.send(peer_id, _refusal_frame(SkatRefusal.BAD_LINK))
        elif not self._stages.attach(room_id, source, peer_id):
            await self._rooms.send(peer_id, _refusal_frame(SkatRefusal.BUSY))
        else:
            await logger.ainfo("skat_attached", room_id=room_id, attachment=self._stages.state_of(room_id).revision)
            await self._tell_room(room_id)

    async def _on_detach(self, peer_id: PeerId, frame: ForeignFrame) -> None:
        attachment = frame.data.get("attachment")
        if not isinstance(attachment, int) or isinstance(attachment, bool):
            raise InvalidMessageError("A detach needs the attachment's number")
        room_id = self._rooms.room_of(peer_id)
        held = None if room_id is None else self._stages.state_of(room_id).attachment
        if room_id is None or held is None or held.number != attachment:
            return
        if held.opener not in (None, peer_id) and not self._rooms.is_host(peer_id):
            await self._rooms.send(peer_id, _refusal_frame(SkatRefusal.NOT_OPENER_OR_HOST))
            return
        self._stages.detach(room_id)
        await logger.ainfo("skat_detached", room_id=room_id, attachment=attachment)
        await self._tell_room(room_id)

    def _table_frame(self, room_id: str) -> JsonObject:
        """The room's state as it is at this call, so a frame sent late never claims more than it was sent with."""
        state = self._stages.state_of(room_id)
        held = state.attachment
        return {
            TYPE_KEY: SkatSignal.SKAT_TABLE.value,
            "revision": state.revision,
            "attachment": None if held is None else held.number,
            "src": None if held is None else held.source,
            "opener_id": None if held is None else held.opener,
        }

    async def _tell_room(self, room_id: str) -> None:
        for member in self._rooms.members(room_id):
            await self._rooms.send(member, self._table_frame(room_id))


@dataclass(frozen=True)
class BuiltSkatFeature:
    """What Skat adds to the app: its call feature, and no route and no bearer resource."""

    factory: FeatureFactory
    router: APIRouter
    bearer_resource: ProtectedResource | None = None


@dataclass(frozen=True)
class SkatFeature:
    """Skat, built inside `create_app`: the call may frame the tables of one origin."""

    origin: str

    @property
    def sign_in_scopes(self) -> tuple[str, ...]:
        """None: the frame signs in at Skat itself."""
        return ()

    @property
    def frame_sources(self) -> tuple[str, ...]:
        """The one origin the page may frame."""
        return (self.origin,)

    def __call__(
        self,
        *,
        relying_party: OidcRelyingParty,
        sessions: SessionRecordStore,
        auth_config: WebAuthConfig,
        scheduler: Scheduler,
        transaction: SignInTransaction,
    ) -> BuiltSkatFeature:
        del relying_party, sessions, auth_config, scheduler, transaction
        return BuiltSkatFeature(partial(Skat, origin=self.origin), APIRouter())


def configured_skat_feature(public_origin: str) -> tuple[SkatFeature, ...]:
    """Skat as the environment configures it: one feature for a configured origin, none for an unset one."""
    configured = SkatSettings().origin
    origins = () if configured is None else (configured,)
    for origin in origins:
        check_origin_differs(origin, public_origin)
    return tuple(SkatFeature(origin) for origin in origins)
