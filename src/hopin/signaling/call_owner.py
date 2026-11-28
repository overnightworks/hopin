"""This process's hold on the calls it serves, and the care for the calls a lost process held.

A fresh holder id per start keeps the lease of a process that restarts distinct from its last one. While the process
runs, its lease is renewed, and every process serves the lapses of the others: a lapse frees the calls its generation
held (seats, agent places, departures nobody told, rooms in grace that generation timed). A process whose lease lapsed
while it stalled and lives on holds a new generation, and the sockets of its old one are closed. A seat opened by a
commit whose reply never came, which no socket holds, is swept.
"""

import asyncio
import contextlib
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

import structlog
from tandem import StoreError

from hopin.config.constants import (
    CALL_LAPSE_SWEEP_SECONDS,
    CALL_LEASE_RENEWAL_SECONDS,
    CALL_LEASE_START_RETRY_SECONDS,
    SERVER_ERROR_CLOSE_CODE,
    SESSION_RECHECK_SECONDS,
)
from hopin.signaling.transition import Change, RoomView

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from tandem import Hold, Lapse

    from hopin.rooms.models import Departure
    from hopin.signaling.call_store import ProcessLease
    from hopin.signaling.frames import PeerId
    from hopin.signaling.transition import Calls

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class CallRouting(Protocol):
    """What the owner asks of the room routing above it: the leave, the announcement of a departure and the end of a
    room in grace."""

    async def leave(self, peer_id: PeerId, *, lapsed: bool = False) -> Departure | None: ...

    async def announce(self, departure: Departure | None) -> None: ...

    async def end_graced_room(self, room_id: str, generation: str) -> None: ...


class CallServices(Protocol):
    """The handler as the owner sees it: the calls it commits to and the routing it frees seats through."""

    @property
    def calls(self) -> Calls: ...

    @property
    def routing(self) -> CallRouting: ...


class CallOwner:
    def __init__(self, handler: CallServices, lease: ProcessLease) -> None:
        self._handler = handler
        self._lease = lease
        self._previous_round: set[PeerId] = set()

    @contextlib.asynccontextmanager
    async def serving(self) -> AsyncIterator[None]:
        """Hold the lease, serve the lapses and watch the process while the block runs. A graceful stop frees its own
        generation's calls before the lease ends, so a stop and a lapse free by one path. The block starts after the
        first try at the lease; a Redis that does not answer then keeps the process running, and it is not ready until
        the lease is held."""
        first_try = asyncio.Event()
        holding = asyncio.create_task(self._hold(first_try))
        try:
            await first_try.wait()
            if holding.done():
                holding.result()  # a failure other than an unanswering Redis ends the start
            yield
        finally:
            holding.cancel()
            await asyncio.gather(holding, return_exceptions=True)

    async def _hold(self, first_try: asyncio.Event) -> None:
        async with contextlib.AsyncExitStack() as kept:
            hold = await self._acquire(kept, first_try)
            self._lease.held_as(hold)
            background = [
                asyncio.create_task(self._serve_lapses()),
                asyncio.create_task(self._every(CALL_LEASE_RENEWAL_SECONDS, self._watch(hold))),
                asyncio.create_task(self._every(SESSION_RECHECK_SECONDS, self.sweep)),
            ]
            try:
                await asyncio.Event().wait()
            finally:
                for task in background:
                    task.cancel()
                await asyncio.gather(*background, return_exceptions=True)
                await self.free(hold.generation)

    async def _acquire(self, kept: contextlib.AsyncExitStack, first_try: asyncio.Event) -> Hold:
        """Enter the lease's hold, retried until Redis answers."""
        every = timedelta(seconds=CALL_LEASE_RENEWAL_SECONDS)
        try:
            while True:
                try:
                    return await kept.enter_async_context(self._lease.leases.kept(self._lease.holder, every=every))
                except StoreError:
                    logger.warning("call_owner.lease_not_held")
                    first_try.set()
                    await asyncio.sleep(CALL_LEASE_START_RETRY_SECONDS)
        finally:
            first_try.set()

    def _watch(self, hold: Hold) -> Callable[[], Awaitable[None]]:
        known = hold.generation

        async def step() -> None:
            nonlocal known
            if hold.generation != known:
                known = hold.generation
                await self.close_sockets_of_old_generations()

        return step

    async def _serve_lapses(self) -> None:
        async def handle(lapse: Lapse) -> None:
            await self.free(lapse.generation)

        await self._lease.leases.serve_lapses(handle, every=timedelta(seconds=CALL_LAPSE_SWEEP_SECONDS))

    @staticmethod
    async def _every(seconds: float, step: Callable[[], Awaitable[None]]) -> None:
        while True:
            await asyncio.sleep(seconds)
            try:
                await step()
            except Exception:
                logger.exception("call_owner.step_failed")

    async def free(self, generation: str) -> None:
        """Free what a generation held: its agent places, its seats (each told to its room), the departures it left
        untold and the rooms in grace that it timed."""
        store = self._handler.calls.store
        routing = self._handler.routing
        for room_id, agent_id in await store.places_of_generation(generation):
            await self._release_place(room_id, agent_id)
        for peer_id, _ in await store.seats_of_generation(generation):
            await routing.announce(await routing.leave(peer_id, lapsed=True))
        for room_id in await store.departures_of_generation(generation):
            room = await store.find_room(room_id)
            for departure in () if room is None else room.departures:
                if departure.generation == generation:
                    await routing.announce(departure)
        for room_id in await store.graces_of_generation(generation):
            await routing.end_graced_room(room_id, generation)

    async def _release_place(self, room_id: str, agent_id: str) -> None:
        calls = self._handler.calls
        change = Change()
        change.release_agent_place(agent_id)
        async with calls.action(room_id):
            await calls.commit(RoomView(room_id=room_id), change)

    async def close_sockets_of_old_generations(self) -> None:
        """Close the sockets of seats opened under a generation this process no longer holds: its lease lapsed while
        it stalled, those seats were freed, and the pages reconnect."""
        for socket in self._handler.calls.seats.sockets_outside(self._lease.generation):
            await self._handler.calls.seats.close_socket(socket.websocket, SERVER_ERROR_CLOSE_CODE)

    async def sweep(self) -> None:
        """One round of freeing the seats of this generation that no socket holds and that are not parked, and were so
        a round before too: a commit whose reply never came may have opened a seat nobody holds. A seat that sits in a
        room leaves through the leave path, so the room hears of it."""
        store = self._handler.calls.store
        attached = self._handler.calls.seats.peers()
        unheld = {
            peer_id
            for peer_id, record in await store.seats_of_generation(self._lease.generation)
            if peer_id not in attached and record.park is None
        }
        swept = unheld & self._previous_round
        self._previous_round = unheld - swept
        routing = self._handler.routing
        for peer_id in swept:
            await routing.announce(await routing.leave(peer_id))
