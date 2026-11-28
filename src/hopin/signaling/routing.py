import math
from functools import partial
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
from hopin.rooms.models import SeatRecord, hash_token
from hopin.rooms.reference import room_reference
from hopin.signaling.call_store import (
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
from hopin.signaling.seats import SeatSocket, mint_peer_id, mint_resume_token
from hopin.signaling.transition import CoreTransition

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Sequence

    from starlette.websockets import WebSocket

    from hopin.errors import HopInError
    from hopin.rooms.models import Account, Departure, HostGrace
    from hopin.signaling.call_store import CallStore
    from hopin.signaling.frames import ResumeClaim
    from hopin.signaling.scheduler import Deadlines
    from hopin.signaling.seats import Connection, Seats
    from hopin.signaling.transition import Calls, Committed

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


def _telling(
    peer_ids: Iterable[str], frame: JsonObject, supersedes: Supersedes | None = None, *, acting: PeerId | None = None
) -> CoreTransition:
    transition = CoreTransition(acting=acting)
    for peer_id in peer_ids:
        transition.send(PeerId(peer_id), frame, supersedes)
    return transition


class RoomRouting:
    """The call core's actions on rooms and seats: each reads the store, decides, and commits one transition, in the
    room's turn and again against what the other processes changed meanwhile."""

    def __init__(
        self,
        calls: Calls,
        deadlines: Deadlines,
        on_seat_left: Callable[[Departure, Sequence[tuple[PeerId, JsonObject]]], Awaitable[list[Committed]]],
    ) -> None:
        self._calls = calls
        self._deadlines = deadlines
        deadlines.register(DeadlineKind.HOST_GRACE, self._expire_grace)
        deadlines.register(DeadlineKind.RESUME_WINDOW, self._expire_park)
        self._on_seat_left = on_seat_left

    @property
    def _store(self) -> CallStore:
        return self._calls.store

    @property
    def _seats(self) -> Seats:
        return self._calls.seats

    async def room_of(self, peer_id: PeerId) -> str | None:
        return await self._store.room_of(peer_id)

    async def _decide[Outcome](
        self, room_id: str | None, decide: Callable[[], Awaitable[tuple[Outcome, Committed | None]]]
    ) -> Outcome:
        """Decide and commit in the room's turn, then wake the seats the commit sent to, outside it."""
        outcome, committed = await self._store.deciding(room_id, decide)
        if committed is not None:
            await self._calls.wake_committed([committed])
        return outcome

    async def open_seat(self, account: Account, websocket: WebSocket) -> PeerId:
        """Opens a seat; a parked seat holds no socket, so only live ones count toward the account's cap."""

        async def decide() -> tuple[PeerId, None]:
            live_seats = sum(1 for _, record in await self._store.held_by(account.id) if record.park is None)
            if live_seats >= MAX_LIVE_SEATS_PER_ACCOUNT:
                raise TooManyCallsError
            peer_id = mint_peer_id()
            token = mint_resume_token()
            opening = CoreTransition()
            opening.edit(SeatOpened(peer_id, SeatRecord(account, hash_token(token), self._store.generation)))
            opening.attach_socket(peer_id, SeatSocket(websocket, unsent_token=token))
            await self._calls.commit_edits(opening)
            return peer_id, None

        return await self._decide(None, decide)

    async def send_error(self, peer_id: PeerId, error: HopInError) -> None:
        await self._tell(_telling([peer_id], error_frame(error), acting=peer_id))

    async def _tell(self, telling: CoreTransition) -> None:
        """Commit frames to seats, which no record the decision read changes."""

        async def decide() -> tuple[None, Committed]:
            return None, await self._calls.commit_core(telling)

        await self._decide(None, decide)

    async def handle_join(self, peer_id: PeerId, frame: JoinFrame) -> None:
        if await self._store.room_of(peer_id) is not None:
            raise InvalidMessageError("This seat is already in a room")

        if frame.room_id is None:
            await self._create_room(peer_id)
        else:
            await self._join_room(peer_id, frame.room_id)

    async def _create_room(self, peer_id: PeerId) -> None:
        async def decide() -> tuple[str, Committed]:
            room_id = await self._store.new_room_id()
            creating = CoreTransition(acting=peer_id)
            creating.edit(RoomCreated(room_id, peer_id))
            creating.send(
                peer_id, KeptRoomCreatedFrame(room_id=room_id, is_host=True, participant_id=peer_id).to_json()
            )
            return room_id, await self._calls.commit_core(creating)

        room_id = await self._decide(None, decide)
        await logger.ainfo("room.created", room_ref=room_reference(room_id), peer_id=peer_id)

    async def _join_room(self, peer_id: PeerId, room_id: str) -> None:
        async def decide() -> tuple[None, Committed]:
            account = (await self._store.record_of(peer_id)).account
            room = await self._store.get_room(room_id)
            room.check_admission(account, self._store.place_limit)
            host_returned = room.is_hosted_by(account) and room.grace is not None
            grace = None if host_returned else room.grace
            others = [pid for pid in room.participant_ids if pid != peer_id]
            names = {participant.participant_id: participant.account.name for participant in room.participants}

            joining = CoreTransition(acting=peer_id)
            joining.edit(ParticipantAdded(room_id, peer_id))
            if host_returned:
                joining.edit(HostGraceCleared(room_id))
            joining.send(
                peer_id,
                KeptExistingParticipantsFrame(
                    participants=others,
                    names={pid: names[pid] for pid in others},
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
            return None, await self._calls.commit_core(joining)

        await self._decide(room_id, decide)
        await logger.ainfo("participant.joined", room_ref=room_reference(room_id), peer_id=peer_id)

    async def set_locked(self, peer_id: PeerId, *, locked: bool) -> None:
        room_id = await self._store.room_of(peer_id)
        if not room_id:
            return

        async def decide() -> tuple[None, Committed]:
            room = await self._store.get_room(room_id)
            frame = RoomLockedFrame() if locked else RoomUnlockedFrame()
            locking = _telling(room.participant_ids, frame.to_json(), acting=peer_id)
            locking.edit(RoomLockSet(room_id, peer_id, locked=locked))
            return None, await self._calls.commit_core(locking)

        await self._decide(room_id, decide)
        if locked:
            await logger.ainfo("room.locked", room_ref=room_reference(room_id), peer_id=peer_id)
        else:
            await logger.ainfo("room.unlocked", room_ref=room_reference(room_id), peer_id=peer_id)

    async def end_room_unnamed(self, room_id: str) -> None:
        """Ends the call at once for everyone as its host's end does, but without the host's name, so no new copy of
        it leaves."""
        await self._end_room(room_id, RoomClosedReason.ENDED, names_host=False)

    async def handle_end_room(self, peer_id: PeerId) -> None:
        room_id = await self._store.room_of(peer_id)
        if not room_id:
            return
        (await self._store.get_room(room_id)).check_hosted_by((await self._store.record_of(peer_id)).account)
        await self._end_room(room_id, RoomClosedReason.ENDED)

    async def handle_media_state(self, peer_id: PeerId, frame: MediaStateFrame) -> None:
        room_id = await self._store.room_of(peer_id)
        if not room_id:
            return
        others = [pid for pid in (await self._store.get_room(room_id)).participant_ids if pid != peer_id]
        broadcast = MediaStateBroadcastFrame(from_=peer_id, audio=frame.audio, video=frame.video)
        await self._tell(_telling(others, broadcast.to_json(), acting=peer_id))

    async def handle_connection_outcome(self, peer_id: PeerId, frame: ConnectionOutcomeFrame) -> None:
        """Write down how one of a seat's peer connections went: the room and the closed outcome only. A seat in no
        room, and a report beyond the seat's rate, are dropped."""
        room_id = await self._store.room_of(peer_id)
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
        sender_room = await self._store.room_of(peer_id)
        if sender_room is None or await self._store.room_of(PeerId(target_id)) != sender_room:
            await logger.ainfo("relay.refused", reason="outside_room", peer_id=peer_id)
            return
        relayed = RELAY_BROADCAST_FRAMES[frame.kind](sdp=frame.sdp, candidate=frame.candidate, from_=peer_id)
        await self._tell(_telling([target_id], relayed.to_json(), acting=peer_id))

    async def park(self, peer_id: PeerId) -> None:
        """Hold the seat for a resume after its socket dropped."""
        room_id = await self._store.room_of(peer_id)

        async def decide() -> tuple[None, None]:
            parking = CoreTransition()
            parking.edit(SeatParked(peer_id, self._deadlines.now()))
            await self._calls.commit_edits(parking)
            return None, None

        await self._decide(room_id, decide)
        await logger.ainfo("seat.parked", peer_id=peer_id)

    async def _expire_park(self, peer_id: str) -> None:
        await self.handle_disconnect(PeerId(peer_id))

    async def handle_disconnect(self, peer_id: PeerId) -> None:
        await self.announce(await self.leave(peer_id))

    async def leave(self, peer_id: PeerId, *, lapsed: bool = False) -> Departure | None:
        """Forgets the seat and its place in the room without suspending the call, so a server's end leaves no seat to
        park or resume; the departure it returns, which the room keeps until it is told, is announced separately. A seat
        gone already leaves nothing. The last seat a lapse takes from a room takes the room with it, as no host is
        coming back to a room nobody is in."""
        room_id = await self._store.room_of(peer_id)

        async def decide() -> tuple[Departure | None, None]:
            record = await self._store.find_record(peer_id)
            if record is None:
                return None, None
            left_at = self._deadlines.now() if record.park is None else record.park.dropped_at
            room = None if record.room_id is None else await self._store.find_room(record.room_id)
            leaving = CoreTransition()
            leaving.edit(SeatForgotten(peer_id))
            departure = None
            if room is not None and peer_id in room.participant_ids:
                departure = room.departure_of(peer_id, self._store.generation)
                leaving.edit(ParticipantRemoved(room.room_id, peer_id))
                if lapsed and not departure.remaining:
                    leaving.edit(RoomEnded(room.room_id, ()))
                elif departure.host_left:
                    leaving.edit(HostGraceStarted(room.room_id, peer_id, left_at + HOST_GRACE_SECONDS))
            await self._calls.commit_edits(leaving)
            return departure, None

        return await self._decide(room_id, decide)

    async def announce(self, departure: Departure | None) -> None:
        """Tells the room of a departure it has not heard of: the leave and a lapse both call it, and whichever comes
        first tells the room and the other then tells nothing."""
        if departure is None:
            return

        committed: list[Committed] = []
        async with self._store.action(departure.room_id):
            room = await self._store.find_room(departure.room_id)
            # Read now, not at the leave: a host back before the announcement has cleared the grace.
            stored = None if room is None else room.departure_naming(departure.peer_id)
            if room is None or stored is None:
                return
            told: list[tuple[PeerId, JsonObject]] = []
            for peer_id in stored.remaining:
                if stored.host_left and room.grace is not None:
                    told.append((PeerId(peer_id), self._host_away_frame(room.grace).to_json()))
                told.append((PeerId(peer_id), ParticipantLeftFrame(participant_id=PeerId(stored.peer_id)).to_json()))
            committed = await self._on_seat_left(stored, told)
        await self._calls.wake_committed(committed)
        await logger.ainfo("participant.left", room_ref=room_reference(departure.room_id), peer_id=departure.peer_id)

    async def _expire_grace(self, room_id: str) -> None:
        room = await self._store.find_room(room_id)
        # Only the generation that timed the grace ends the room: a host back on any process cleared it.
        if room is not None and room.grace is not None and room.grace.generation == self._store.generation:
            await self._end_room(room_id, RoomClosedReason.HOST_GONE)

    async def end_graced_room(self, room_id: str, generation: str) -> None:
        """Ends a room whose host grace the lost generation timed, which nobody else times now."""
        room = await self._store.find_room(room_id)
        if room is not None and room.grace is not None and room.grace.generation == generation:
            await self._end_room(room_id, RoomClosedReason.HOST_GONE)

    async def _end_room(self, room_id: str, reason: RoomClosedReason, *, names_host: bool = True) -> None:
        async def decide() -> tuple[list[PeerId] | None, Committed | None]:
            room = await self._store.find_room(room_id)
            if room is None:
                return None, None
            recipients = [PeerId(peer_id) for peer_id in room.participant_ids]
            grace_seconds = HOST_GRACE_SECONDS if reason is RoomClosedReason.HOST_GONE else None
            frame = RoomClosedFrame(
                reason=reason, host_name=room.host.name if names_host else "", grace_seconds=grace_seconds
            )
            # The call is over, so it supersedes whatever of the call a seat has not been written yet.
            closing = _telling(recipients, frame.to_json(), Supersedes(types=None))
            closing.edit(RoomEnded(room_id, tuple(recipients)))
            return recipients, await self._calls.commit_core(closing)

        recipients = await self._decide(room_id, decide)
        if recipients is None:
            return
        sockets = [socket for peer_id in recipients if (socket := self._seats.find(peer_id)) is not None]
        forgetting = CoreTransition()
        for peer_id in recipients:
            forgetting.edit(SeatForgotten(peer_id))
        await self._store.deciding(None, partial(self._forget, forgetting))
        for socket in sockets:
            await self._seats.close_socket(socket.websocket)
        await logger.ainfo("room.closed", room_ref=room_reference(room_id), reason=reason.value)

    async def _forget(self, forgetting: CoreTransition) -> None:
        await self._calls.commit_edits(forgetting)

    def _host_away_frame(self, grace: HostGrace) -> HostAwayFrame:
        return HostAwayFrame(
            seconds_left=math.ceil(grace.deadline - self._deadlines.now()), participant_id=PeerId(grace.host_peer_id)
        )

    async def resume(self, connection: Connection, frame: ResumeFrame, *, is_first_frame: bool) -> None:
        peer_id = PeerId("")
        try:
            if not is_first_frame:
                raise ResumeRefusedError(ResumeRefusalCause.NOT_FIRST_FRAME)
            claim = frame.require_claim()
            peer_id = claim.participant_id
            taken = await self._decide(
                await self._store.room_of(peer_id), partial(self._take_over_seat, connection, claim)
            )
        except ResumeRefusedError as refusal:
            if refusal.cause in {ResumeRefusalCause.SERVER_LOG_TOO_SHORT, ResumeRefusalCause.BROWSER_LOG_TOO_SHORT}:
                await self._release_unresumable_seat(peer_id)
            await logger.ainfo("resume.refused", peer_id=connection.peer_id, reason=refusal.cause.value)
            await self._seats.write(connection.websocket, error_frame(refusal))
            return
        connection.peer_id = peer_id
        superseded, socket, token, last_seq = taken
        if superseded is not None:
            await self._seats.close_socket(superseded.websocket)
        await self._seats.write(connection.websocket, ResumedFrame(resume_token=token, last_seq=last_seq).to_json())
        if not await self._seats.replay(peer_id, socket, claim.last_seq):
            await self._release_unresumable_seat(peer_id)
        await logger.ainfo("seat.resumed", peer_id=peer_id)

    async def _take_over_seat(
        self, connection: Connection, claim: ResumeClaim
    ) -> tuple[tuple[SeatSocket | None, SeatSocket, str, int], Committed]:
        peer_id = claim.participant_id
        record = await self._matching_seat(connection, claim)
        gap = _gap_before_resume(record, claim)
        if gap is not None:
            raise ResumeRefusedError(gap)
        superseded = self._seats.find(peer_id)
        token = mint_resume_token()
        socket = SeatSocket(connection.websocket, replaying=True)
        taking_over = CoreTransition()
        taking_over.edit(SeatForgotten(connection.peer_id))
        taking_over.edit(SeatTakenOver(peer_id, hash_token(token)))
        taking_over.attach_socket(peer_id, socket)
        committed = await self._calls.commit_core(taking_over)
        return (superseded, socket, token, record.received_seq), committed

    async def _matching_seat(self, connection: Connection, claim: ResumeClaim) -> SeatRecord:
        record = await self._store.find_record(claim.participant_id)
        # Before a seat survives its process, the generation that owns it is the one that can resume it.
        if record is None or record.generation != self._store.generation:
            raise ResumeRefusedError(ResumeRefusalCause.UNKNOWN_SEAT)
        if not record.accepts(claim.token):
            raise ResumeRefusedError(ResumeRefusalCause.CREDENTIAL_MISMATCH)
        if (await self._store.record_of(connection.peer_id)).account.id != record.account.id:
            raise ResumeRefusedError(ResumeRefusalCause.WRONG_ACCOUNT)
        return record

    async def _release_unresumable_seat(self, peer_id: PeerId) -> None:
        socket = self._seats.find(peer_id)
        await self.handle_disconnect(peer_id)
        if socket is not None:
            await self._seats.close_socket(socket.websocket)


def _gap_before_resume(record: SeatRecord, claim: ResumeClaim) -> ResumeRefusalCause | None:
    if not record.reaches_back_to(claim.last_seq):
        return ResumeRefusalCause.SERVER_LOG_TOO_SHORT
    if claim.sent_floor > record.received_seq:
        return ResumeRefusalCause.BROWSER_LOG_TOO_SHORT
    return None
