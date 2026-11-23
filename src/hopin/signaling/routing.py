import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from hopin.config.constants import (
    CONNECTION_OUTCOME_RATE,
    HOST_GRACE_SECONDS,
    MAX_LIVE_SEATS_PER_ACCOUNT,
    ResumeRefusalCause,
    RoomClosedReason,
)
from hopin.config.rate import admitted
from hopin.errors import InvalidMessageError, ResumeRefusedError, TooManyCallsError
from hopin.rooms.reference import room_reference
from hopin.signaling.frames import (
    RELAY_BROADCAST_FRAMES,
    ConnectionOutcomeFrame,
    HostAwayFrame,
    HostBackFrame,
    JoinFrame,
    JsonObject,
    KeptExistingParticipantsFrame,
    KeptRoomCreatedFrame,
    MediaStateBroadcastFrame,
    MediaStateFrame,
    ParticipantJoinedFrame,
    ParticipantLeftFrame,
    PeerId,
    RelayFrame,
    ResumedFrame,
    ResumeFrame,
    RoomClosedFrame,
    RoomLockedFrame,
    RoomUnlockedFrame,
    error_frame,
)
from hopin.signaling.replay import Supersedes
from hopin.signaling.scheduler import DeadlineKind
from hopin.signaling.seats import SeatRecord, SeatSocket, hash_token, mint_peer_id, mint_resume_token
from hopin.signaling.transition import (
    CoreTransition,
    HostGraceCleared,
    HostGraceStarted,
    ParticipantAdded,
    ParticipantRemoved,
    RoomCreated,
    RoomEnded,
    RoomLockSet,
    SeatForgotten,
    SeatOpened,
    SeatParked,
    SeatTakenOver,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Sequence

    from starlette.websockets import WebSocket

    from hopin.errors import HopInError
    from hopin.rooms.manager import RoomManager
    from hopin.rooms.models import Account, HostGrace
    from hopin.signaling.frames import ResumeClaim
    from hopin.signaling.scheduler import Deadlines
    from hopin.signaling.seats import Connection, Seats
    from hopin.signaling.transition import InMemoryCallStore

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


@dataclass(frozen=True)
class Departure:
    """What the rest of a room must hear after a seat left it."""

    room_id: str
    peer_id: PeerId
    name: str
    remaining: list[str]
    host_left: bool


def _telling(peer_ids: Iterable[str], frame: JsonObject, supersedes: Supersedes | None = None) -> CoreTransition:
    transition = CoreTransition()
    for peer_id in peer_ids:
        transition.send(PeerId(peer_id), frame, supersedes)
    return transition


class RoomRouting:
    """The call core's actions on rooms and seats: each reads the store, decides, and commits one transition."""

    def __init__(
        self,
        room_manager: RoomManager,
        store: InMemoryCallStore,
        deadlines: Deadlines,
        on_seat_left: Callable[[Departure, Sequence[tuple[PeerId, JsonObject]]], Awaitable[None]],
    ) -> None:
        self._room_manager = room_manager
        self._store = store
        self._deadlines = deadlines
        deadlines.register(DeadlineKind.HOST_GRACE, self._expire_grace)
        deadlines.register(DeadlineKind.RESUME_WINDOW, self._expire_park)
        self._on_seat_left = on_seat_left

    @property
    def _seats(self) -> Seats:
        return self._store.seats

    def room_of(self, peer_id: PeerId) -> str | None:
        return self._store.room_of(peer_id)

    def open_seat(self, account: Account, websocket: WebSocket) -> PeerId:
        """Opens a seat; a parked seat holds no socket, so only live ones count toward the account's cap."""
        live_seats = sum(1 for _, record in self._store.held_by(account.id) if record.park is None)
        if live_seats >= MAX_LIVE_SEATS_PER_ACCOUNT:
            raise TooManyCallsError
        peer_id = mint_peer_id()
        token = mint_resume_token()
        opening = CoreTransition()
        opening.edit(
            SeatOpened(peer_id, SeatRecord(account, hash_token(token)), SeatSocket(websocket, unsent_token=token))
        )
        self._store.commit_edits(opening)
        return peer_id

    async def send_error(self, peer_id: PeerId, error: HopInError) -> None:
        await self._store.apply(_telling([peer_id], error_frame(error)))

    async def handle_join(self, peer_id: PeerId, frame: JoinFrame) -> None:
        if self._store.room_of(peer_id) is not None:
            raise InvalidMessageError("This seat is already in a room")

        if frame.room_id is None:
            await self._create_room(peer_id)
        else:
            await self._join_room(peer_id, frame.room_id)

    async def _create_room(self, peer_id: PeerId) -> None:
        room_id = self._room_manager.new_room_id()
        creating = CoreTransition()
        creating.edit(RoomCreated(room_id, peer_id))
        creating.send(peer_id, KeptRoomCreatedFrame(room_id=room_id, is_host=True, participant_id=peer_id).to_json())
        await self._store.apply(creating)
        await logger.ainfo("room.created", room_ref=room_reference(room_id), peer_id=peer_id)

    async def _join_room(self, peer_id: PeerId, room_id: str) -> None:
        account = self._store.record_of(peer_id).account
        room = self._room_manager.check_admission(room_id, account)
        host_returned = room.is_hosted_by(account) and room.grace is not None
        grace = None if host_returned else room.grace
        others = [pid for pid in room.participant_ids if pid != peer_id]

        joining = CoreTransition()
        joining.edit(ParticipantAdded(room_id, peer_id))
        if host_returned:
            joining.edit(HostGraceCleared(room_id))
        joining.send(
            peer_id,
            KeptExistingParticipantsFrame(
                participants=others,
                names={pid: self._store.record_of(PeerId(pid)).account.name for pid in others},
                is_host=room.is_hosted_by(account),
                locked=room.locked,
                participant_id=peer_id,
            ).to_json(),
        )
        if grace is not None:
            joining.send(peer_id, self._host_away_frame(grace).to_json())
        for other in others:
            joining.send(PeerId(other), ParticipantJoinedFrame(participant_id=peer_id, name=account.name).to_json())
        if host_returned:
            for other in others:
                joining.send(PeerId(other), HostBackFrame(participant_id=peer_id).to_json())
        await self._store.apply(joining)

        await logger.ainfo("participant.joined", room_ref=room_reference(room_id), peer_id=peer_id)

    async def set_locked(self, peer_id: PeerId, *, locked: bool) -> None:
        room_id = self._store.room_of(peer_id)
        if not room_id:
            return
        room = self._room_manager.get_room(room_id)
        frame = RoomLockedFrame() if locked else RoomUnlockedFrame()
        locking = _telling(room.participant_ids, frame.to_json())
        locking.edits.append(RoomLockSet(room_id, peer_id, locked=locked))
        await self._store.apply(locking)
        if locked:
            await logger.ainfo("room.locked", room_ref=room_reference(room_id), peer_id=peer_id)
        else:
            await logger.ainfo("room.unlocked", room_ref=room_reference(room_id), peer_id=peer_id)

    async def end_room_unnamed(self, room_id: str) -> None:
        """Ends the call at once for everyone as its host's end does, but without the host's name, so no new copy of
        it leaves."""
        await self._end_room(room_id, RoomClosedReason.ENDED, names_host=False)

    async def handle_end_room(self, peer_id: PeerId) -> None:
        room_id = self._store.room_of(peer_id)
        if not room_id:
            return
        self._room_manager.get_room_for_host(room_id, self._store.record_of(peer_id).account)
        await self._end_room(room_id, RoomClosedReason.ENDED)

    async def handle_media_state(self, peer_id: PeerId, frame: MediaStateFrame) -> None:
        room_id = self._store.room_of(peer_id)
        if not room_id:
            return
        others = [pid for pid in self._room_manager.get_room(room_id).participant_ids if pid != peer_id]
        broadcast = MediaStateBroadcastFrame(from_=peer_id, audio=frame.audio, video=frame.video)
        await self._store.apply(_telling(others, broadcast.to_json()))

    async def handle_connection_outcome(self, peer_id: PeerId, frame: ConnectionOutcomeFrame) -> None:
        """Write down how one of a seat's peer connections went: the room and the closed outcome only. A seat in no
        room, and a report beyond the seat's rate, are dropped."""
        room_id = self._store.room_of(peer_id)
        if room_id is None:
            return
        socket = self._seats.socket_of(peer_id)
        window = admitted(socket.outcome_times, self._deadlines.now(), CONNECTION_OUTCOME_RATE)
        if window is None:
            return
        socket.outcome_times = window
        await logger.ainfo("connection.reported", room_ref=room_reference(room_id), outcome=frame.outcome.value)

    async def handle_relay(self, peer_id: PeerId, frame: RelayFrame) -> None:
        target_id = frame.target
        if target_id is None:
            return
        sender_room = self._store.room_of(peer_id)
        if sender_room is None or self._store.room_of(PeerId(target_id)) != sender_room:
            await logger.ainfo("relay.refused", reason="outside_room", peer_id=peer_id)
            return
        relayed = RELAY_BROADCAST_FRAMES[frame.kind](sdp=frame.sdp, candidate=frame.candidate, from_=peer_id)
        await self._store.apply(_telling([target_id], relayed.to_json()))

    async def park(self, peer_id: PeerId) -> None:
        """Hold the seat for a resume after its socket dropped."""
        parking = CoreTransition()
        parking.edit(SeatParked(peer_id))
        self._store.commit_edits(parking)
        await logger.ainfo("seat.parked", peer_id=peer_id)

    async def _expire_park(self, peer_id: str) -> None:
        await self.handle_disconnect(PeerId(peer_id))

    async def handle_disconnect(self, peer_id: PeerId) -> None:
        await self.announce(self.leave(peer_id))

    def leave(self, peer_id: PeerId) -> Departure | None:
        """Forgets the seat and its place in the room without awaiting, so a server's end leaves no seat to park
        or resume; the departure it returns is announced separately."""
        record = self._store.record_of(peer_id)
        left_at = self._deadlines.now() if record.park is None else record.park.dropped_at
        leaving = CoreTransition()
        leaving.edit(SeatForgotten(peer_id))
        departure = self._departure(peer_id, record.room_id)
        if departure is not None:
            leaving.edit(ParticipantRemoved(departure.room_id, peer_id))
            if departure.host_left:
                leaving.edit(HostGraceStarted(departure.room_id, peer_id, left_at + HOST_GRACE_SECONDS))
        self._store.commit_edits(leaving)
        return departure

    def _departure(self, peer_id: PeerId, room_id: str | None) -> Departure | None:
        room = None if room_id is None else self._room_manager.find_room(room_id)
        if room is None:
            return None
        leaver = room.participants[peer_id]
        return Departure(
            room_id=room.room_id,
            peer_id=peer_id,
            name=leaver.account.name,
            remaining=[pid for pid in room.participant_ids if pid != peer_id],
            host_left=room.is_hosted_by(leaver.account) and not room.host_present_without(peer_id),
        )

    async def announce(self, departure: Departure | None) -> None:
        if departure is None:
            return
        told: list[tuple[PeerId, JsonObject]] = []
        # Read now, not at the leave: a host back before the announcement has cleared the grace.
        room = self._room_manager.find_room(departure.room_id)
        grace = None if room is None else room.grace
        for peer_id in departure.remaining:
            if departure.host_left and grace is not None:
                told.append((PeerId(peer_id), self._host_away_frame(grace).to_json()))
            told.append((PeerId(peer_id), ParticipantLeftFrame(participant_id=departure.peer_id).to_json()))
        await logger.ainfo("participant.left", room_ref=room_reference(departure.room_id), peer_id=departure.peer_id)
        await self._on_seat_left(departure, told)

    async def _expire_grace(self, room_id: str) -> None:
        await self._end_room(room_id, RoomClosedReason.HOST_GONE)

    async def _end_room(self, room_id: str, reason: RoomClosedReason, *, names_host: bool = True) -> None:
        room = self._room_manager.get_room(room_id)
        recipients = [PeerId(peer_id) for peer_id in room.participant_ids]
        grace_seconds = HOST_GRACE_SECONDS if reason is RoomClosedReason.HOST_GONE else None
        frame = RoomClosedFrame(
            reason=reason, host_name=room.host.name if names_host else "", grace_seconds=grace_seconds
        )
        # The call is over, so it supersedes whatever of the call a seat has not been written yet.
        closing = _telling(recipients, frame.to_json(), Supersedes(types=None))
        closing.edits.append(RoomEnded(room_id, tuple(recipients)))
        await self._store.apply(closing)
        sockets = [socket for peer_id in recipients if (socket := self._seats.find(peer_id)) is not None]
        forgetting = CoreTransition()
        forgetting.edits.extend(SeatForgotten(peer_id) for peer_id in recipients)
        self._store.commit_edits(forgetting)
        for socket in sockets:
            await self._seats.close_socket(socket.websocket)
        await logger.ainfo("room.closed", room_ref=room_reference(room_id), reason=reason.value)

    def _host_away_frame(self, grace: HostGrace) -> HostAwayFrame:
        return HostAwayFrame(
            seconds_left=math.ceil(grace.deadline - self._deadlines.now()), participant_id=PeerId(grace.host_peer_id)
        )

    async def resume(self, connection: Connection, frame: ResumeFrame, *, is_first_frame: bool) -> None:
        try:
            if not is_first_frame:
                raise ResumeRefusedError(ResumeRefusalCause.NOT_FIRST_FRAME)
            claim = frame.require_claim()
            record = self._matching_seat(connection, claim)
            gap = _gap_before_resume(record, claim)
            if gap is not None:
                await self._release_unresumable_seat(claim.participant_id)
                raise ResumeRefusedError(gap)
        except ResumeRefusedError as refusal:
            await logger.ainfo("resume.refused", peer_id=connection.peer_id, reason=refusal.cause.value)
            await self._seats.write(connection.websocket, error_frame(refusal))
            return
        await self._take_over_seat(connection, record, claim)

    def _matching_seat(self, connection: Connection, claim: ResumeClaim) -> SeatRecord:
        record = self._store.find_record(claim.participant_id)
        if record is None:
            raise ResumeRefusedError(ResumeRefusalCause.UNKNOWN_SEAT)
        if not record.accepts(claim.token):
            raise ResumeRefusedError(ResumeRefusalCause.CREDENTIAL_MISMATCH)
        if self._store.record_of(connection.peer_id).account.id != record.account.id:
            raise ResumeRefusedError(ResumeRefusalCause.WRONG_ACCOUNT)
        return record

    async def _release_unresumable_seat(self, peer_id: PeerId) -> None:
        websocket = self._seats.socket_of(peer_id).websocket
        await self.handle_disconnect(peer_id)
        await self._seats.close_socket(websocket)

    async def _take_over_seat(self, connection: Connection, record: SeatRecord, claim: ResumeClaim) -> None:
        peer_id = claim.participant_id
        superseded = self._seats.socket_of(peer_id).websocket
        token = mint_resume_token()
        socket = SeatSocket(connection.websocket, replaying=True)
        taking_over = CoreTransition()
        taking_over.edit(SeatForgotten(connection.peer_id))
        taking_over.edit(SeatTakenOver(peer_id, hash_token(token), socket))
        self._store.commit_edits(taking_over)
        connection.peer_id = peer_id

        await self._seats.close_socket(superseded)
        await self._seats.write(
            connection.websocket, ResumedFrame(resume_token=token, last_seq=record.received_seq).to_json()
        )
        if not await self._seats.replay(peer_id, socket, claim.last_seq):
            await self._release_unresumable_seat(peer_id)
        await logger.ainfo("seat.resumed", peer_id=peer_id)


def _gap_before_resume(record: SeatRecord, claim: ResumeClaim) -> ResumeRefusalCause | None:
    if not record.log.reaches_back_to(claim.last_seq):
        return ResumeRefusalCause.SERVER_LOG_TOO_SHORT
    if claim.sent_floor > record.received_seq:
        return ResumeRefusalCause.BROWSER_LOG_TOO_SHORT
    return None
