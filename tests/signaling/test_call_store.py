"""The call store over a Redis: what its one commit writes, what it refuses and what it fences."""

import asyncio
from unittest.mock import patch

import pytest
from fakeredis import FakeAsyncRedis
from tandem import Script

from hopin.config.constants import (
    COMMIT_ATTEMPTS,
    REPLAY_LOG_LIMIT,
    STORED_FRAME_MAX_BYTES,
    SignalType,
)
from hopin.errors import RoomFullError, RoomLockedError
from hopin.rooms.models import Account, SeatRecord, hash_token
from hopin.signaling.call_store import (
    Commit,
    CommitRefusal,
    CommitRefusedError,
    DepartureAlreadyToldError,
    DepartureTold,
    HostGraceStarted,
    NotServingError,
    Outbound,
    ParticipantAdded,
    ParticipantRemoved,
    RoomCreated,
    RoomEnded,
    SeatForgotten,
    SeatOpened,
    SeatTakenOver,
    VersionConflictError,
    call_leases,
)
from hopin.signaling.frames import SENDER_KEY, PeerId
from hopin.signaling.replay import Supersedes
from tests.stores.support.real_redis import ON_FAKEREDIS_AND_REAL_REDIS
from tests.support.call_state import (
    call_store_over,
    end_room,
    host_room,
    lock_room,
    open_seat,
    redis_client,
    seat_in,
    seat_of,
    take_agent_place,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, ON_FAKEREDIS_AND_REAL_REDIS]

CALL_KEYS = "hopin:v1:call:*"


def relay(sender: PeerId, text: str, **fields: object) -> dict[str, object]:
    return {"type": SignalType.MEDIA_STATE.value, SENDER_KEY: sender, "text": text, **fields}


async def keys_of(server) -> set[bytes]:
    return set(await redis_client(server).keys(CALL_KEYS))


async def commits_elsewhere(store, request) -> None:
    """Commit as another decision would, in a task that holds none of the records the caller read."""
    await asyncio.create_task(store.commit(request))


async def logged(store, peer_id, after_seq=0) -> list[dict]:
    return [entry.frame for entry in (await store.read_log(peer_id, after_seq)).frames]


@pytest.fixture
def other_process(redis_server):
    """A second process over the same Redis, under a generation of its own."""
    return call_store_over(redis_server)


class TestTheCommit:
    async def test_a_call_is_created_joined_left_and_ended(self, call_store):
        room = await host_room(call_store, "host")
        guest = await seat_in(call_store, room.room_id, "guest")

        joined = await call_store.get_room(room.room_id)
        await call_store.commit(Commit(edits=(ParticipantRemoved(room.room_id, guest),)))
        left = await call_store.get_room(room.room_id)
        await end_room(call_store, room.room_id)

        assert (joined.participant_ids, left.participant_ids) == ([seat_of("host"), guest], [seat_of("host")])
        assert await call_store.find_room(room.room_id) is None

    async def test_frames_are_logged_in_seq_order_and_read_after_a_seq(self, call_store):
        host = await open_seat(call_store, "host")
        frames = tuple(Outbound(host, relay(PeerId("other"), f"frame {number}")) for number in range(3))

        woken = await call_store.commit(Commit(frames=frames))
        await call_store.commit(Commit(frames=(Outbound(host, relay(PeerId("other"), "frame 3")),)))

        assert woken == (host,)
        assert [frame["text"] for frame in await logged(call_store, host)] == [f"frame {n}" for n in range(4)]
        assert [frame["text"] for frame in await logged(call_store, host, after_seq=2)] == ["frame 2", "frame 3"]

    async def test_a_description_supersedes_its_senders_earlier_descriptions_and_candidates(self, call_store):
        host = await open_seat(call_store, "host")
        peer = PeerId("peer")
        earlier = [
            {"type": SignalType.OFFER.value, SENDER_KEY: peer, "sdp": "first"},
            {"type": SignalType.ICE_CANDIDATE.value, SENDER_KEY: peer, "candidate": "a"},
            {"type": SignalType.ICE_CANDIDATE.value, SENDER_KEY: "another", "candidate": "b"},
        ]
        await call_store.commit(Commit(frames=tuple(Outbound(host, frame) for frame in earlier)))

        await call_store.commit(Commit(frames=(Outbound(host, {"type": SignalType.ANSWER.value, SENDER_KEY: peer}),)))

        assert [(frame["type"], frame[SENDER_KEY]) for frame in await logged(call_store, host)] == [
            (SignalType.ICE_CANDIDATE.value, "another"),
            (SignalType.ANSWER.value, peer),
        ]

    async def test_a_frame_supersedes_the_earlier_frames_of_its_own_agent_only(self, call_store):
        host = await open_seat(call_store, "host")
        thinking = [{"type": "agent_state", "agent_id": agent, "state": "thinking"} for agent in ("one", "two")]
        await call_store.commit(Commit(frames=tuple(Outbound(host, frame) for frame in thinking)))

        supersedes = Supersedes(frozenset({"agent_state"}), holding=("agent_id", "one"))
        await call_store.commit(
            Commit(frames=(Outbound(host, {"type": "agent_state", "agent_id": "one", "state": "idle"}, supersedes),))
        )

        assert [(frame["agent_id"], frame["state"]) for frame in await logged(call_store, host)] == [
            ("two", "thinking"),
            ("one", "idle"),
        ]

    async def test_a_log_keeps_its_newest_frames_and_a_resume_before_them_is_not_reachable(self, call_store):
        host = await open_seat(call_store, "host")
        total = REPLAY_LOG_LIMIT + 5
        for number in range(total):
            await call_store.commit(Commit(frames=(Outbound(host, relay(PeerId("other"), str(number))),)))

        read = await call_store.read_log(host, 0)

        assert (await call_store.log_length(host), read.frames[-1].seq, read.floor > 0) == (
            REPLAY_LOG_LIMIT,
            total,
            True,
        )

    async def test_an_ended_call_leaves_no_key_that_stays(self, redis_server, call_store):
        room = await host_room(call_store, "host")
        guest = await seat_in(call_store, room.room_id, "guest")
        await call_store.commit(
            Commit(
                frames=(Outbound(guest, relay(PeerId("other"), "hello")),),
                edits=(RoomEnded(room.room_id, (guest, seat_of("host"))),),
            )
        )
        await call_store.commit(Commit(edits=(SeatForgotten(guest), SeatForgotten(seat_of("host")))))

        remaining = await keys_of(redis_server)
        survivors = {key for key in remaining if await redis_client(redis_server).ttl(key) < 0}

        assert survivors == set()

    async def test_no_key_holds_a_resume_token(self, redis_server, call_store):
        token = "plaintext-token"  # noqa: S105 - a value the store must never write
        record = SeatRecord(Account(id="host", name="host"), hash_token(token), call_store.generation)
        await call_store.commit(Commit(edits=(SeatOpened(PeerId("host-seat"), record),)))

        redis = redis_client(redis_server)
        written = [await redis.hgetall(key) for key in await keys_of(redis_server) if await redis.type(key) == b"hash"]

        assert token.encode() not in repr(written).encode()

    async def test_a_resume_of_another_account_does_not_take_the_seat(self, call_store):
        """CALL-10."""
        host = await open_seat(call_store, "host")

        record = await call_store.record_of(host)

        assert not record.accepts("another-token")


class TestAdmission:
    async def test_call_02_a_locked_room_refuses_a_newcomer(self, call_store):
        """CALL-02: A locked call admits nobody new."""
        room = await host_room(call_store, "host")
        await lock_room(call_store, room.room_id, seat_of("host"))

        with pytest.raises(RoomLockedError):
            await seat_in(call_store, room.room_id, "stranger")

    async def test_call_07_an_away_hosts_place_stays_reserved_against_a_newcomer(self, call_store):
        """CALL-07: An away host keeps a place, so a newcomer cannot take the last of the places."""
        room = await host_room(call_store, "host")
        for name in ("first", "second", "third"):
            await seat_in(call_store, room.room_id, name)
        await call_store.commit(Commit(edits=(ParticipantRemoved(room.room_id, seat_of("host")),)))
        await seat_in(call_store, room.room_id, "fourth")

        with pytest.raises(RoomFullError):
            await seat_in(call_store, room.room_id, "fifth")


class TestAFrameBeyondTheCap:
    async def test_a_server_built_frame_over_the_stored_cap_commits_nothing(self, call_store):
        host = await open_seat(call_store, "host")
        too_large = relay(PeerId("other"), "x" * (STORED_FRAME_MAX_BYTES + 1))

        with pytest.raises(CommitRefusedError) as refusal:
            await call_store.commit(Commit(frames=(Outbound(host, too_large),), edits=(RoomCreated("r", host),)))

        assert refusal.value.refusal is CommitRefusal.FRAME_TOO_LARGE
        assert (await call_store.find_room("r"), await call_store.log_length(host)) == (None, 0)


class TestTheLeaseFence:
    async def test_a_commit_after_the_lease_lapsed_opens_no_seat_and_takes_no_place(self, redis_server, call_store):
        room = await host_room(call_store, "host")
        lease = call_store._lease  # noqa: SLF001 - the lease the store commits under
        await lease.leases.end(lease.holder, lease.generation)

        with pytest.raises(CommitRefusedError) as refusal:
            await open_seat(call_store, "late")
        with pytest.raises(CommitRefusedError):
            await take_agent_place(call_store, room.room_id, "agent")

        assert refusal.value.refusal is CommitRefusal.LEASE
        assert (await call_store.get_room(room.room_id)).agent_ids == frozenset()
        assert (await call_store.held_by("late")) == ()

    async def test_a_process_that_holds_no_lease_yet_commits_nothing(self, redis_server):
        from hopin.signaling.call_store import CallStore, ProcessLease

        store = CallStore(
            redis_client(redis_server), ProcessLease(call_leases(FakeAsyncRedis()), "unheld"), place_limit=6
        )

        with pytest.raises(NotServingError):
            await open_seat(store, "host")


class TestTheGenerationFence:
    async def test_a_commit_by_a_seat_another_generation_owns_writes_nothing(
        self, redis_server, call_store, other_process
    ):
        host = await open_seat(call_store, "host")

        with pytest.raises(CommitRefusedError) as refusal:
            await other_process.commit(Commit(edits=(RoomCreated("r", host),)))

        assert refusal.value.refusal is CommitRefusal.FENCE
        assert await call_store.find_room("r") is None

    async def test_a_relay_from_a_seat_on_one_process_is_written_into_the_log_of_a_seat_on_another(
        self, call_store, other_process
    ):
        sender = await open_seat(call_store, "sender")
        target = await open_seat(other_process, "target")

        await call_store.commit(Commit(acting=sender, frames=(Outbound(target, relay(sender, "hi")),)))

        assert [frame["text"] for frame in await logged(other_process, target)] == ["hi"]

    async def test_a_relay_to_a_seat_gone_writes_nothing(self, redis_server, call_store):
        sender = await open_seat(call_store, "sender")
        gone = PeerId("gone-seat")

        await call_store.commit(Commit(acting=sender, frames=(Outbound(gone, relay(sender, "hi")),)))

        assert await call_store.log_length(gone) == 0
        assert await call_store.find_record(gone) is None


class TestAgentPlaces:
    async def test_a_stale_release_by_an_old_agent_id_frees_nothing_else(self, call_store):
        room = await host_room(call_store, "host")
        await take_agent_place(call_store, room.room_id, "agent-new")

        await call_store.commit(Commit(room_id=room.room_id, places_released=("agent-old",)))

        assert (await call_store.get_room(room.room_id)).agent_ids == frozenset({"agent-new"})

    async def test_a_place_is_listed_under_the_generation_that_took_it(self, call_store):
        room = await host_room(call_store, "host")
        await take_agent_place(call_store, room.room_id, "agent")

        assert await call_store.places_of_generation(call_store.generation) == ((room.room_id, "agent"),)


class TestConcurrentDecisions:
    async def test_two_processes_opening_an_accounts_next_seat_leave_one_the_seat(self, call_store, other_process):
        """CALL-42."""
        await open_seat(call_store, "account")
        async with call_store.action(None), other_process.action(None):
            await call_store.held_by("account")
            await other_process.held_by("account")
            first = SeatRecord(Account(id="account", name="account"), hash_token("a"), call_store.generation)
            second = SeatRecord(Account(id="account", name="account"), hash_token("b"), other_process.generation)
            await call_store.commit(Commit(edits=(SeatOpened(PeerId("first"), first),)))

            with pytest.raises(VersionConflictError):
                await other_process.commit(Commit(edits=(SeatOpened(PeerId("second"), second),)))

        assert len(await call_store.held_by("account")) == 2

    async def test_two_joins_of_the_last_place_leave_one_the_place(self, call_store, other_process):
        """CALL-07: Two processes join the last place of a call; one gets it."""
        room = await host_room(call_store, "host")
        for name in ("first", "second", "third"):
            await seat_in(call_store, room.room_id, name)
        late_seats = [await open_seat(call_store, name) for name in ("fourth", "fifth")]
        async with call_store.action(room.room_id), other_process.action(room.room_id):
            await call_store.get_room(room.room_id)
            await other_process.get_room(room.room_id)
            await call_store.commit(Commit(edits=(ParticipantAdded(room.room_id, late_seats[0]),)))

            with pytest.raises(VersionConflictError):
                await other_process.commit(Commit(edits=(ParticipantAdded(room.room_id, late_seats[1]),)))

        assert (await call_store.get_room(room.room_id)).participant_ids[-1] == late_seats[0]

    async def test_two_resumes_of_one_seat_take_it_once_and_the_account_version_moves_once(
        self, call_store, other_process
    ):
        """CALL-10."""
        seat = await open_seat(call_store, "host")
        before = await call_store.account_version("host")
        async with call_store.action(None):
            await call_store.record_of(seat)
            await commits_elsewhere(call_store, Commit(edits=(SeatTakenOver(seat, hash_token("first")),)))

            with pytest.raises(VersionConflictError):
                await call_store.commit(Commit(edits=(SeatTakenOver(seat, hash_token("second")),)))

        assert (await call_store.account_version("host")) - before == 1
        assert (await call_store.record_of(seat)).accepts("first")

    async def test_a_core_transition_retries_a_conflict_and_fails_loud_after_the_third(self, call_store):
        seat = await open_seat(call_store, "host")
        attempts = []

        async def decide():
            attempts.append(await call_store.record_of(seat))
            await commits_elsewhere(call_store, Commit(edits=(SeatTakenOver(seat, hash_token(str(len(attempts)))),)))
            await call_store.commit(Commit(edits=(SeatTakenOver(seat, hash_token("mine")),)))

        with pytest.raises(VersionConflictError):
            await call_store.deciding(None, decide)

        assert len(attempts) == COMMIT_ATTEMPTS

    async def test_a_hooks_commit_meeting_a_conflict_fails_loud_and_the_hook_ran_once(self, call_store):
        seat = await open_seat(call_store, "host")
        ran = []

        async def hook_and_commit():
            await call_store.record_of(seat)
            ran.append(True)
            await commits_elsewhere(call_store, Commit(edits=(SeatTakenOver(seat, hash_token("other")),)))
            await call_store.commit(Commit(edits=(SeatTakenOver(seat, hash_token("hook")),)))

        async with call_store.action(None):
            with pytest.raises(VersionConflictError):
                await hook_and_commit()

        assert ran == [True]

    async def test_a_burst_of_candidates_to_one_seat_meets_no_conflict(self, call_store, other_process):
        target = await open_seat(call_store, "target")
        sender = await open_seat(other_process, "sender")
        candidate = {"type": SignalType.ICE_CANDIDATE.value, SENDER_KEY: sender}

        await asyncio.gather(
            *(
                (call_store if number % 2 else other_process).commit(
                    Commit(frames=(Outbound(target, {**candidate, "candidate": str(number)}),))
                )
                for number in range(50)
            )
        )

        assert await call_store.log_length(target) == 50


class TestAReplayedCommit:
    @pytest.fixture
    def sent_twice(self):
        """A client that resent the script after a reply that never came: the same call reaches Redis twice."""
        run = Script.run

        async def run_twice(script, keys, arguments):
            await run(script, keys, arguments)
            return await run(script, keys, arguments)

        with patch.object(Script, "run", run_twice):
            yield

    async def test_a_relay_applies_once(self, call_store, sent_twice):
        host = await open_seat(call_store, "host")

        await call_store.commit(Commit(frames=(Outbound(host, relay(PeerId("other"), "once")),)))

        assert await call_store.log_length(host) == 1

    async def test_the_open_of_an_accounts_seat_applies_once(self, call_store, sent_twice):
        await open_seat(call_store, "account")

        assert len(await call_store.held_by("account")) == 1

    async def test_a_leave_applies_once_and_keeps_one_departure(self, call_store, sent_twice):
        room = await host_room(call_store, "host")
        guest = await seat_in(call_store, room.room_id, "guest")

        await call_store.commit(Commit(edits=(ParticipantRemoved(room.room_id, guest),)))

        assert [d.peer_id for d in (await call_store.get_room(room.room_id)).departures] == [guest]


class TestADepartureTold:
    async def test_the_room_is_told_of_a_departure_once(self, call_store, other_process):
        room = await host_room(call_store, "host")
        guest = await seat_in(call_store, room.room_id, "guest")
        await call_store.commit(Commit(edits=(ParticipantRemoved(room.room_id, guest),)))

        await other_process.commit(Commit(edits=(DepartureTold(room.room_id, guest),)))

        with pytest.raises(DepartureAlreadyToldError):
            await call_store.commit(Commit(edits=(DepartureTold(room.room_id, guest),)))
        assert (await call_store.get_room(room.room_id)).departures == ()

    async def test_a_seat_that_left_is_no_seat_of_its_account_and_cannot_be_resumed(self, call_store):
        """CALL-10."""
        room = await host_room(call_store, "host")
        guest = await seat_in(call_store, room.room_id, "guest")

        await call_store.commit(Commit(edits=(ParticipantRemoved(room.room_id, guest), SeatForgotten(guest))))

        assert (await call_store.held_by("guest"), await call_store.find_record(guest)) == ((), None)


class TestARoomInGrace:
    async def test_a_room_in_grace_is_listed_under_the_generation_that_times_it(self, call_store):
        """CALL-01, CALL-11."""
        room = await host_room(call_store, "host")
        await call_store.commit(Commit(edits=(HostGraceStarted(room.room_id, seat_of("host"), 123.0),)))

        assert await call_store.graces_of_generation(call_store.generation) == (room.room_id,)


class TestARoomId:
    async def test_a_minted_room_id_names_no_live_room(self, call_store, monkeypatch):
        room = await host_room(call_store, "host")
        candidates = iter([room.room_id[0], room.room_id[1:]])

        class Ids:
            @staticmethod
            def choice(_alphabet):
                return next(candidates)

        monkeypatch.setattr("hopin.signaling.call_store.secrets", Ids)
        monkeypatch.setattr("hopin.signaling.call_store.ROOM_ID_LENGTH", 1)

        minted = await call_store.new_room_id()

        assert minted != room.room_id
