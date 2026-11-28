"""A call store over the Redis of a test, under a process lease of its own; each store over a server is a process."""

import asyncio
import secrets
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta

from fakeredis import FakeAsyncRedis, FakeServer
from redis.asyncio import Redis
from tandem import Hold

from hopin.rooms.models import Account, Room, SeatRecord, hash_token
from hopin.signaling.call_store import (
    CallStore,
    Commit,
    ParticipantAdded,
    ProcessLease,
    RoomCreated,
    RoomEnded,
    RoomLockSet,
    SeatOpened,
    call_leases,
)
from hopin.signaling.frames import PeerId
from tests.support.signaling_fakes import ROOM_LIMIT

# A test never waits for a lease to lapse; it ends one with `Leases.end`.
LEASE_LASTS = timedelta(hours=1)


def redis_client(server: FakeServer | str) -> Redis:
    """A client of the Redis a test's processes share: a fakeredis server, or the url of a real Redis."""
    return FakeAsyncRedis(server=server) if isinstance(server, FakeServer) else Redis.from_url(server)


async def _hold(server: FakeServer | str, holder: str) -> str:
    seeding = redis_client(server)
    try:
        return (await call_leases(seeding, LEASE_LASTS).renew(holder)).generation
    finally:
        await seeding.aclose()


def started_lease(server: FakeServer | str) -> ProcessLease:
    """The lease of a process that has started on the server and holds its calls; it starts on a loop of its own, so a
    test that runs a loop can start a process too."""
    holder = secrets.token_hex(8)
    lease = ProcessLease(call_leases(redis_client(server), LEASE_LASTS), holder)
    with ThreadPoolExecutor(max_workers=1) as seeding:
        lease.held_as(Hold(generation=seeding.submit(asyncio.run, _hold(server, holder)).result()))
    return lease


@dataclass(frozen=True)
class Process:
    """One process over a Redis: its store and the lease it commits under."""

    store: CallStore
    lease: ProcessLease


def process_over(server: FakeServer | str, *, place_limit: int = ROOM_LIMIT) -> Process:
    lease = started_lease(server)
    return Process(CallStore(redis_client(server), lease, place_limit=place_limit), lease)


def call_store_over(server: FakeServer | str, *, place_limit: int = ROOM_LIMIT) -> CallStore:
    """The call store of one more process over the server."""
    return process_over(server, place_limit=place_limit).store


async def open_seat(store: CallStore, name: str) -> PeerId:
    """A seat of the account of that name, which no socket holds."""
    peer_id = seat_of(name)
    record = SeatRecord(Account(id=name, name=name), hash_token(f"{name}-token"), store.generation)
    await store.commit(Commit(edits=(SeatOpened(peer_id, record),)))
    return peer_id


async def host_room(store: CallStore, host: str) -> Room:
    """A room hosted by the account of that name, who sits in it with a seat no socket holds."""
    peer_id = await open_seat(store, host)
    room_id = await store.new_room_id()
    await store.commit(Commit(edits=(RoomCreated(room_id, peer_id),)))
    return await store.get_room(room_id)


def seat_of(name: str) -> PeerId:
    """The seat `host_room` and `seat_in` opened for the account of that name."""
    return PeerId(f"{name}-seat")


async def seat_in(store: CallStore, room_id: str, name: str) -> PeerId:
    """A seat of the account of that name, which no socket holds, in the room."""
    peer_id = await open_seat(store, name)
    await store.commit(Commit(edits=(ParticipantAdded(room_id, peer_id),)))
    return peer_id


async def lock_room(store: CallStore, room_id: str, host_seat: PeerId) -> None:
    await store.commit(Commit(edits=(RoomLockSet(room_id, host_seat, locked=True),)))


async def end_room(store: CallStore, room_id: str) -> None:
    await store.commit(Commit(edits=(RoomEnded(room_id, ()),)))


async def take_agent_place(store: CallStore, room_id: str, agent_id: str) -> None:
    await store.commit(Commit(room_id=room_id, places_taken=(agent_id,)))
