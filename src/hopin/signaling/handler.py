from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, assert_never

import structlog
from logline import unit_trace
from starlette.websockets import WebSocket  # noqa: TC002 - FastAPI resolves the route annotation at runtime

from hopin.config.constants import (
    NORMAL_CLOSE_CODES,
    POLICY_VIOLATION_CLOSE_CODE,
    SERVER_ERROR_CLOSE_CODE,
    SESSION_ENDED_CLOSE_CODE,
    SESSION_RECHECK_SECONDS,
    WEBSOCKET_DISCONNECT_EVENT,
)
from hopin.errors import HopInError, InvalidMessageError, TooManyCallsError
from hopin.signaling.call_store import COMMIT_FAILURES, DepartureTold
from hopin.signaling.features import TransitionHost, build_features
from hopin.signaling.frames import (
    ConnectionOutcomeFrame,
    DispatchedFrame,
    EndRoomFrame,
    ForeignFrame,
    JoinFrame,
    LockFrame,
    MediaStateFrame,
    PeerId,
    RelayFrame,
    ResumeFrame,
    UnlockFrame,
    decode_frame,
    error_frame,
)
from hopin.signaling.routing import RoomRouting
from hopin.signaling.seats import Connection, SeatSnapshot
from hopin.signaling.transition import Calls, Committed, CoreTransition, RoomView

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hopin.rooms.models import Departure, SeatRecord
    from hopin.signaling.call_store import CallStore
    from hopin.signaling.features import TransitionFeature
    from hopin.signaling.frames import JsonObject
    from hopin.signaling.identity import IdentityResolver
    from hopin.signaling.scheduler import Deadlines, Scheduler, TimerHandle

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


@dataclass(frozen=True)
class PersonReach:
    """What an account holds in this process: its seats, and the calls it hosts that others take part in."""

    seats: int
    calls: int


class SignalingHandler:
    def __init__(
        self,
        call_store: CallStore,
        identity_resolver: IdentityResolver,
        scheduler: Scheduler,
        deadlines: Deadlines,
        features: Sequence[TransitionFeature] = (),
    ) -> None:
        self._identity_resolver = identity_resolver
        self._scheduler = scheduler
        self._store = call_store
        self._calls = Calls(call_store, deadlines, self._room_ended, self._seat_changed)
        self._routing = RoomRouting(self._calls, deadlines, self._seat_left)
        self._seats = self._calls.seats
        self._features = build_features(features, self._calls, self._seats)
        self._feature_of_type = {
            signal_type: feature for feature in self._features for signal_type in feature.signal_types.values()
        }

    @property
    def features(self) -> tuple[TransitionHost, ...]:
        return self._features

    @property
    def calls(self) -> Calls:
        return self._calls

    @property
    def routing(self) -> RoomRouting:
        return self._routing

    def _room_ended(self, room_id: str) -> None:
        for feature in self._features:
            feature.room_ended(room_id)

    async def _seat_changed(self, peer_id: PeerId) -> None:
        for feature in self._features:
            await feature.seat_changed(peer_id)

    async def _seat_left(
        self, departure: Departure, told_frames: Sequence[tuple[PeerId, JsonObject]]
    ) -> list[Committed]:
        """The room is told of the departure in one commit with each feature's answer to it, so a feature's frame
        supersedes its own outdated ones before any reaches a seat. The room hears of a departure once: the commit
        that tells it also takes the departure from the room."""
        told = CoreTransition()
        told.edit(DepartureTold(departure.room_id, PeerId(departure.peer_id)))
        for peer_id, frame in told_frames:
            told.send(peer_id, frame)
        committed = [Committed(await self._calls.commit(RoomView(), told))]
        for feature in self._features:
            committed += await feature.commit_seat_left(
                PeerId(departure.peer_id), room_id=departure.room_id, name=departure.name
            )
        return committed

    async def shut_down(self) -> None:
        """Shut the call's features down in order and wait until each has cleaned up; the calls' feature state ends with
        them."""
        for feature in self._features:
            await feature.shut_down()
        self._calls.end_all()

    async def seats_of(self, account_id: str) -> tuple[SeatSnapshot, ...]:
        """The seats an account holds, on every process, parked ones included."""
        return tuple(
            [await self._snapshot_of(peer_id, record) for peer_id, record in await self._store.held_by(account_id)]
        )

    async def reach_of(self, account_id: str) -> PersonReach:
        """What the account holds on every process. A call the account hosts counts when another seat or an agent is in
        it; a host alone in its grace changes nothing for others."""
        hosted = await self._store.hosted_by(account_id)
        return PersonReach(
            seats=len(await self._store.held_by(account_id)), calls=sum(room.has_guests for room in hosted)
        )

    async def end_all_of(self, account_id: str) -> PersonReach:
        """Ends the calls the account hosts at once for everyone, unnamed, then closes every seat it still holds as a
        session end does, a parked one included, and answers what was there when it began. A seat whose socket another
        process holds is gone from the store, and that process closes its socket at its next commit or minute check."""
        reach = await self.reach_of(account_id)
        # Each pass reads again, because a call or a seat can end on its own while an earlier one is being closed.
        while hosted := await self._store.hosted_by(account_id):
            await self._routing.end_room_unnamed(hosted[0].room_id)
        while held := await self._store.held_by(account_id):
            peer_id, _ = held[0]
            socket = self._seats.find(peer_id)
            await self._end_seat(peer_id, None if socket is None else socket.websocket, SESSION_ENDED_CLOSE_CODE)
        return reach

    async def _snapshot_of(self, peer_id: PeerId, record: SeatRecord) -> SeatSnapshot:
        room_id = record.room_id
        room = None if room_id is None else await self._store.find_room(room_id)
        return SeatSnapshot(
            call_id=room_id,
            host=room is not None and room.is_hosted_by(record.account),
            replay_frames=await self._store.log_length(peer_id),
        )

    async def handle_websocket(self, websocket: WebSocket) -> None:
        account = await self._identity_resolver.resolve(websocket)
        if account is None:
            await websocket.close(code=POLICY_VIOLATION_CLOSE_CODE)
            return

        await websocket.accept()
        try:
            peer_id = await self._routing.open_seat(account, websocket)
        except TooManyCallsError as refusal:
            await self._refuse_beyond_cap(websocket, refusal)
            return
        await logger.ainfo("client.connected", peer_id=peer_id)

        connection = Connection(websocket=websocket, peer_id=peer_id)
        connection.recheck = self._schedule_recheck(connection)
        try:
            await self._process_messages(connection)
        finally:
            await self._end_connection(connection)

    async def _refuse_beyond_cap(self, websocket: WebSocket, refusal: TooManyCallsError) -> None:
        await logger.ainfo("client.refused", reason="too_many_calls", **refusal.frame_fields())
        await self._seats.write(websocket, error_frame(refusal))
        await self._seats.close_socket(websocket, POLICY_VIOLATION_CLOSE_CODE)

    async def _process_messages(self, connection: Connection) -> None:
        serving = True
        while serving:
            serving = await self._process_next_message(connection)

    async def _process_next_message(self, connection: Connection) -> bool:
        """Serve the next frame of the socket, and say whether to go on. The frame is a local of this call alone, so no
        frame's text outlives its handling while the loop awaits the next one."""
        frame = await connection.websocket.receive()
        if frame["type"] == WEBSOCKET_DISCONNECT_EVENT:
            if frame["code"] not in NORMAL_CLOSE_CODES and not self._seats.closed_by_server(connection.websocket):
                connection.dropped = True
                await logger.aerror("websocket.failed", peer_id=connection.peer_id, code=frame["code"])
            return False
        text = frame.get("text")
        if text is not None and self._serves_seat(connection):
            with unit_trace(None):
                try:
                    await self._handle_message(connection, text)
                except COMMIT_FAILURES as failure:
                    await self._fail_seat(connection, failure)
                    return False
        return True

    async def _fail_seat(self, connection: Connection, failure: Exception) -> None:
        """A commit this process cannot make, because it lost its lease, a store did not answer or a frame was refused,
        closes the acting socket; the page reconnects and resumes the seat if this process still holds it."""
        await logger.aerror("call_commit.failed", peer_id=connection.peer_id, error_type=type(failure).__name__)
        connection.dropped = True
        await self._seats.close_socket(connection.websocket, SERVER_ERROR_CLOSE_CODE)

    def _schedule_recheck(self, connection: Connection) -> TimerHandle:
        return self._scheduler.call_later(SESSION_RECHECK_SECONDS, partial(self._recheck_on_tick, connection))

    async def _recheck_on_tick(self, connection: Connection) -> None:
        if connection.ended:
            return
        try:
            account = await self._identity_resolver.resolve(connection.websocket)
        except Exception:
            await self._park_after_store_error(connection)
            return
        if account is None:
            await self._end_session(connection, SESSION_ENDED_CLOSE_CODE)
        elif self._serves_seat(connection):
            connection.recheck = self._schedule_recheck(connection)

    async def _park_after_store_error(self, connection: Connection) -> None:
        """A session store that cannot answer parks the seat as a drop does; the page reconnects and resumes it."""
        # A check can be suspended across a drop or a takeover; the seat then belongs to someone else.
        if not self._serves_seat(connection):
            return
        await logger.aexception("session.check_failed", peer_id=connection.peer_id)
        connection.dropped = True
        connection.ended = True
        await self._seats.close_socket(connection.websocket, SERVER_ERROR_CLOSE_CODE)

    def _holds_seat(self, connection: Connection) -> bool:
        socket = self._seats.find(connection.peer_id)
        return socket is not None and socket.websocket is connection.websocket

    def _serves_seat(self, connection: Connection) -> bool:
        return not connection.ended and self._holds_seat(connection)

    async def _end_session(self, connection: Connection, close_code: int) -> None:
        # A check can be suspended across a drop or a takeover; the seat then belongs to someone else.
        if not self._serves_seat(connection):
            return
        connection.ended = True
        await self._end_seat(connection.peer_id, connection.websocket, close_code)

    async def _end_seat(self, peer_id: PeerId, websocket: WebSocket | None, close_code: int) -> None:
        # The seat leaves before anything suspends, so a drop or a resume read meanwhile finds no seat; the room
        # hears of it only after the close, so another participant's slow socket cannot hold this one open.
        departure = await self._routing.leave(peer_id)
        if websocket is not None:
            await self._seats.close_socket(websocket, close_code)
        await self._routing.announce(departure)
        await logger.ainfo("session.expired", peer_id=peer_id)

    async def _end_connection(self, connection: Connection) -> None:
        connection.ended = True
        connection.recheck.cancel()
        if not self._holds_seat(connection):
            return
        # Outside the socket's session trace, like a frame: the line a leaver cuts wakes another agent's turn.
        with unit_trace(None):
            try:
                if connection.dropped and await self._routing.room_of(connection.peer_id) is not None:
                    await self._routing.park(connection.peer_id)
                else:
                    await self._routing.handle_disconnect(connection.peer_id)
            except COMMIT_FAILURES as failure:
                # The seat stays in the store for the lapse or the sweep of this process to free.
                await logger.aerror("call_end.failed", peer_id=connection.peer_id, error_type=type(failure).__name__)

    async def _handle_message(self, connection: Connection, raw_data: str) -> None:
        peer_id = connection.peer_id
        is_first_frame = connection.frames_received == 0
        connection.frames_received += 1

        try:
            incoming = decode_frame(raw_data)
        except InvalidMessageError as refusal:
            await self._send_error(peer_id, refusal)
            return

        if isinstance(incoming.frame, ResumeFrame):
            await self._routing.resume(connection, incoming.frame, is_first_frame=is_first_frame)
            return

        if not is_first_frame:
            if incoming.sequence is None:
                await self._send_error(peer_id, InvalidMessageError("Missing sequence number"))
                return
            if incoming.sequence <= (await self._store.record_of(peer_id)).received_seq:
                return
            self._calls.receive(peer_id, incoming.sequence)

        try:
            if isinstance(incoming.frame, InvalidMessageError):
                raise incoming.frame
            await self._dispatch(peer_id, incoming.frame)
        except HopInError as exc:
            await self._send_error(peer_id, exc)
        finally:
            await self._calls.settle_received(peer_id)

    async def _dispatch(self, peer_id: PeerId, frame: DispatchedFrame) -> None:
        match frame:
            case JoinFrame():
                await self._routing.handle_join(peer_id, frame)
                for feature in self._features:
                    await feature.seat_joined(peer_id)
            case RelayFrame():
                await self._routing.handle_relay(peer_id, frame)
            case LockFrame():
                await self._routing.set_locked(peer_id, locked=True)
            case UnlockFrame():
                await self._routing.set_locked(peer_id, locked=False)
            case MediaStateFrame():
                await self._routing.handle_media_state(peer_id, frame)
            case ConnectionOutcomeFrame():
                await self._routing.handle_connection_outcome(peer_id, frame)
            case EndRoomFrame():
                await self._routing.handle_end_room(peer_id)
            case ForeignFrame():
                await self._offer_to_feature(peer_id, frame)
            case _:  # pragma: no cover - the type checker proves the match exhaustive
                assert_never(frame)

    async def _offer_to_feature(self, peer_id: PeerId, frame: ForeignFrame) -> None:
        feature = self._feature_of_type.get(frame.signal_type)
        if feature is None or not await feature.on_frame(peer_id, frame):
            raise InvalidMessageError(f"Unknown signal type: {frame.signal_type}")

    async def _send_error(self, peer_id: PeerId, error: HopInError) -> None:
        await self._routing.send_error(peer_id, error)
