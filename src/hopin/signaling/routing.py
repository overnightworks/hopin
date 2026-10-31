import math
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

import structlog

from hopin.config.constants import HOST_GRACE_SECONDS, RoomClosedReason, SignalType
from hopin.errors import InvalidMessageError, RoomNotFoundError
from hopin.signaling.frames import (
    ChatFrame,
    JoinFrame,
    JsonObject,
    MediaStateFrame,
    PeerId,
    RelayFrame,
    RoomClosedFrame,
)
from hopin.signaling.seats import Seats

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from hopin.rooms.manager import RoomManager
    from hopin.signaling.scheduler import Scheduler, TimerHandle

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


@dataclass(frozen=True)
class HostGrace:
    host_peer_id: str
    deadline: float
    timer: TimerHandle


@dataclass(frozen=True)
class Departure:
    """What the rest of a room must hear after a seat left it."""

    room_id: str
    peer_id: PeerId
    name: str
    remaining: list[str]
    host_left: bool


class RoomRouting:
    def __init__(
        self,
        room_manager: RoomManager,
        scheduler: Scheduler,
        on_room_ended: Callable[[str], None],
        on_seat_left: Callable[[Departure], Awaitable[None]],
    ) -> None:
        self._room_manager = room_manager
        self._scheduler = scheduler
        self._on_room_ended = on_room_ended
        self._on_seat_left = on_seat_left
        self._graces: dict[str, HostGrace] = {}
        self._seats = Seats(scheduler, on_seat_lost=self.handle_disconnect)
        self._peer_rooms: dict[str, str] = {}

    @property
    def seats(self) -> Seats:
        return self._seats

    def room_of(self, peer_id: PeerId) -> str | None:
        return self._peer_rooms.get(peer_id)

    async def handle_join(self, peer_id: PeerId, frame: JoinFrame) -> None:
        room_id = frame.room_id
        if peer_id in self._peer_rooms:
            raise InvalidMessageError("This seat is already in a room")

        if room_id is None:
            await self._create_room(peer_id)
        else:
            await self._join_room(peer_id, room_id)

    async def _create_room(self, peer_id: PeerId) -> None:
        room = self._room_manager.create_room(peer_id, self._seats.account_of(peer_id))
        self._peer_rooms[peer_id] = room.room_id
        await self._seats.send(
            peer_id,
            {
                "type": SignalType.ROOM_CREATED.value,
                "room_id": room.room_id,
                "is_host": True,
                **self._seats.credentials(peer_id),
            },
        )
        await logger.ainfo("room_created", room_id=room.room_id, host=peer_id)

    async def _join_room(self, peer_id: PeerId, room_id: str) -> None:
        account = self._seats.account_of(peer_id)
        room = self._room_manager.add_participant(room_id, peer_id, account)
        self._peer_rooms[peer_id] = room_id
        host_returned = room.is_hosted_by(account) and self._cancel_grace(room_id)
        grace = self._graces.get(room_id)
        others = [pid for pid in room.participant_ids if pid != peer_id]

        await self._seats.send(
            peer_id,
            {
                "type": SignalType.EXISTING_PARTICIPANTS.value,
                # A copy: the frame goes into the seat's replay log, and a list[str] is not a JSON value list.
                "participants": [*others],
                "names": {pid: self._seats.account_of(pid).name for pid in others},
                "is_host": room.is_hosted_by(account),
                "locked": room.locked,
                **self._seats.credentials(peer_id),
            },
        )
        if grace is not None:
            await self._seats.send(peer_id, self._host_away_frame(grace))
        await self._seats.send_all(
            others,
            {"type": SignalType.PARTICIPANT_JOINED.value, "participant_id": peer_id, "name": account.name},
        )
        if host_returned:
            await self._seats.send_all(others, {"type": SignalType.HOST_BACK.value, "participant_id": peer_id})

        await logger.ainfo("participant_joined", room_id=room_id, participant=peer_id)

    async def set_locked(self, peer_id: PeerId, *, locked: bool) -> None:
        room_id = self._peer_rooms.get(peer_id)
        if not room_id:
            return
        room = self._room_manager.set_room_locked(room_id, self._seats.account_of(peer_id), locked=locked)
        signal_type = SignalType.ROOM_LOCKED if locked else SignalType.ROOM_UNLOCKED
        await self._broadcast_to_room(room_id, {"type": signal_type.value})
        await logger.ainfo(signal_type.value, room_id=room.room_id, by=peer_id)

    async def handle_end_room(self, peer_id: PeerId) -> None:
        room_id = self._peer_rooms.get(peer_id)
        if not room_id:
            return
        self._room_manager.get_room_for_host(room_id, self._seats.account_of(peer_id))
        await self._end_room(room_id, RoomClosedReason.ENDED)

    async def handle_chat(self, peer_id: PeerId, frame: ChatFrame) -> None:
        room_id = self._peer_rooms.get(peer_id)
        if not room_id:
            return
        message = frame.message
        if not message:
            return
        room = self._room_manager.get_room(room_id)
        others = [pid for pid in room.participant_ids if pid != peer_id]
        await self._seats.send_all(others, {"type": SignalType.CHAT.value, "message": message, "from": peer_id})

    async def handle_media_state(self, peer_id: PeerId, frame: MediaStateFrame) -> None:
        room_id = self._peer_rooms.get(peer_id)
        if not room_id:
            return
        room = self._room_manager.get_room(room_id)
        others = [pid for pid in room.participant_ids if pid != peer_id]
        await self._seats.send_all(
            others,
            {"type": SignalType.MEDIA_STATE.value, "from": peer_id, "audio": frame.audio, "video": frame.video},
        )

    async def handle_relay(self, peer_id: PeerId, frame: RelayFrame) -> None:
        target_id = frame.target
        if target_id is None:
            return
        sender_room = self._peer_rooms.get(peer_id)
        if sender_room is None or self._peer_rooms.get(target_id) != sender_room:
            await logger.ainfo("relay_outside_room", peer_id=peer_id)
            return
        await self._seats.send(
            target_id,
            {
                "type": frame.kind.value,
                "sdp": frame.sdp,
                "candidate": frame.candidate,
                "from": peer_id,
            },
        )

    async def handle_disconnect(self, peer_id: PeerId) -> None:
        await self.announce(self.leave(peer_id))

    def leave(self, peer_id: PeerId) -> Departure | None:
        """Forgets the seat and its place in the room without awaiting, so a server's end leaves no seat to park
        or resume; the departure it returns is announced separately."""
        peer = self._seats.forget(peer_id)
        left_at = self._scheduler.now()
        if peer is not None and peer.park is not None:
            left_at = peer.park.dropped_at
        room_id = self._peer_rooms.pop(peer_id, None)
        if not room_id:
            return None

        try:
            room = self._room_manager.get_room(room_id)
        except RoomNotFoundError:
            return None

        leaver = room.participants[peer_id]
        self._room_manager.remove_participant(room_id, peer_id)
        host_left = room.is_hosted_by(leaver.account) and not room.host_present
        if host_left:
            self._start_grace(room_id, peer_id, left_at + HOST_GRACE_SECONDS)
        return Departure(
            room_id=room_id,
            peer_id=peer_id,
            name=leaver.account.name,
            remaining=room.participant_ids,
            host_left=host_left,
        )

    async def announce(self, departure: Departure | None) -> None:
        if departure is None:
            return
        # Read now, not at the leave: a host back before the announcement has cancelled the grace.
        grace = self._graces.get(departure.room_id)
        if departure.host_left and grace is not None:
            await self._seats.send_all(departure.remaining, self._host_away_frame(grace))
        await self._seats.send_all(
            departure.remaining, {"type": SignalType.PARTICIPANT_LEFT.value, "participant_id": departure.peer_id}
        )
        await logger.ainfo("participant_left", room_id=departure.room_id, participant=departure.peer_id)
        await self._on_seat_left(departure)

    def _start_grace(self, room_id: str, host_peer_id: str, deadline: float) -> None:
        timer = self._scheduler.call_later(deadline - self._scheduler.now(), partial(self._expire_grace, room_id))
        self._graces[room_id] = HostGrace(host_peer_id=host_peer_id, deadline=deadline, timer=timer)

    def _cancel_grace(self, room_id: str) -> bool:
        grace = self._graces.pop(room_id, None)
        if grace is None:
            return False
        grace.timer.cancel()
        return True

    async def _expire_grace(self, room_id: str) -> None:
        if room_id in self._graces:
            await self._end_room(room_id, RoomClosedReason.HOST_GONE)

    async def _end_room(self, room_id: str, reason: RoomClosedReason) -> None:
        recipients = self._room_manager.get_room(room_id).participant_ids
        for peer_id in recipients:
            self._peer_rooms.pop(peer_id, None)
        self._cancel_grace(room_id)
        self._room_manager.remove_room(room_id)
        # Before the first await, so nothing that belongs to the room outlives it by a frame.
        self._on_room_ended(room_id)

        grace_seconds = HOST_GRACE_SECONDS if reason is RoomClosedReason.HOST_GONE else None
        frame = RoomClosedFrame(reason=reason, grace_seconds=grace_seconds)
        await self._seats.send_all(recipients, frame.to_json())
        seats = [self._seats.forget(PeerId(peer_id)) for peer_id in recipients]
        for websocket in [seat.websocket for seat in seats if seat is not None]:
            await self._seats.close_socket(websocket)
        await logger.ainfo("room_closed", room_id=room_id, reason=reason.value)

    def _host_away_frame(self, grace: HostGrace) -> JsonObject:
        return {
            "type": SignalType.HOST_AWAY.value,
            "seconds_left": math.ceil(grace.deadline - self._scheduler.now()),
            "participant_id": grace.host_peer_id,
        }

    async def _broadcast_to_room(self, room_id: str, data: JsonObject) -> None:
        await self._seats.send_all(self._room_manager.get_room(room_id).participant_ids, data)
