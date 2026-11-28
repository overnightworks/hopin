"""What a process does for the calls of a generation that lost its lease: it frees them, once, by one path."""

import asyncio

import pytest
import pytest_asyncio
from fakeredis import FakeAsyncRedis
from tandem import Hold

from hopin.config.constants import SERVER_ERROR_CLOSE_CODE, SignalType
from hopin.signaling.call_owner import CallOwner
from hopin.signaling.call_store import Commit, HostGraceCleared, HostGraceStarted, ParticipantRemoved
from hopin.signaling.handler import SignalingHandler
from hopin.signaling.scheduler import InMemoryDeadlines
from tests.signaling.support.browsers import Link, clear_sent
from tests.support.call_state import open_seat, process_over, seat_of, take_agent_place
from tests.support.signaling_fakes import SocketAccountResolver

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class Pod:
    """A process of the deployment: its handler, the sockets it serves and its owner of calls."""

    def __init__(self, server, scheduler):
        self.process = process_over(server)
        self.handler = SignalingHandler(
            self.process.store, SocketAccountResolver(), scheduler, InMemoryDeadlines(scheduler)
        )
        self.owner = CallOwner(self.handler, self.process.lease)
        self.link = Link(self.handler)

    @property
    def generation(self):
        return self.process.lease.generation

    async def die(self):
        """The lease ends as a killed process's lapses; its sockets are no more."""
        leases = self.process.lease.leases
        await leases.end(self.process.lease.holder, self.generation)


@pytest_asyncio.fixture
async def pods(redis_server, scheduler):
    pair = (Pod(redis_server, scheduler), Pod(redis_server, scheduler))
    yield pair
    for pod in pair:
        await pod.link.close_all()


def types_heard(browser):
    return [frame["type"] for socket in browser.sockets for frame in socket.sent]


async def keys_of_generation(server, generation):
    return [key for key in await FakeAsyncRedis(server=server).keys("hopin:v1:call:*") if generation.encode() in key]


class TestTheLapseOfAGeneration:
    async def test_the_seats_of_a_lost_process_leave_and_the_room_hears_it_by_the_lapse(self, redis_server, pods):
        lost, surviving = pods
        host = await lost.link.join("host")
        guest = await surviving.link.join("guest", host.room_id)
        clear_sent(host, guest)
        generation = lost.generation
        await lost.die()

        await surviving.owner.free(generation)

        assert types_heard(guest) == [SignalType.HOST_AWAY.value, SignalType.PARTICIPANT_LEFT.value]
        assert await surviving.process.store.held_by("host") == ()
        assert await keys_of_generation(redis_server, generation) == []

    async def test_a_room_whose_last_seat_left_by_the_lapse_is_deleted(self, pods):
        lost, surviving = pods
        host = await lost.link.join("host")
        generation = lost.generation
        await lost.die()

        await surviving.owner.free(generation)

        assert await surviving.process.store.find_room(host.room_id) is None

    async def test_every_seat_of_an_account_on_a_lost_process_is_freed_roomless_and_parked_included(self, pods):
        lost, surviving = pods
        roomless = await open_seat(lost.process.store, "account")
        joined = await lost.link.join("account")
        await joined.drop(1000)
        generation = lost.generation
        await lost.die()

        await surviving.owner.free(generation)

        assert (
            await surviving.process.store.find_record(roomless),
            await surviving.process.store.held_by("account"),
        ) == (
            None,
            (),
        )

    async def test_an_agent_place_taken_through_a_lost_process_is_free_after_the_lapse(self, pods):
        lost, surviving = pods
        host = await lost.link.join("host")
        guest = await surviving.link.join("guest", host.room_id)
        await take_agent_place(lost.process.store, host.room_id, "agent")
        generation = lost.generation
        await lost.die()

        await surviving.owner.free(generation)

        assert (await surviving.process.store.get_room(host.room_id)).agent_ids == frozenset()
        assert guest.room_id == host.room_id

    async def test_a_room_in_grace_timed_by_the_lost_process_ends_at_its_lapse(self, pods):
        lost, surviving = pods
        host = await lost.link.join("host")
        guest = await surviving.link.join("guest", host.room_id)
        leaving = (ParticipantRemoved(host.room_id, host.peer_id), HostGraceStarted(host.room_id, host.peer_id, 1.0))
        await lost.process.store.commit(Commit(edits=leaving))
        clear_sent(guest)
        generation = lost.generation
        await lost.die()

        await surviving.owner.free(generation)

        assert await surviving.process.store.find_room(host.room_id) is None
        assert SignalType.ROOM_CLOSED.value in types_heard(guest)

    async def test_a_host_back_on_another_process_keeps_the_room_when_the_old_grace_runs_out(self, pods):
        first, second = pods
        host = await first.link.join("host")
        await first.process.store.commit(Commit(edits=(HostGraceStarted(host.room_id, seat_of("gone"), 1.0),)))
        await second.process.store.commit(Commit(edits=(HostGraceCleared(host.room_id),)))

        await first.handler.routing.end_graced_room(host.room_id, first.generation)

        assert await first.process.store.find_room(host.room_id) is not None


class TestADepartureThePassedProcessNeverTold:
    async def test_the_room_hears_the_departure_once_at_the_lapse_and_the_seat_counts_nowhere(self, pods):
        lost, surviving = pods
        host = await surviving.link.join("host")
        guest = await lost.link.join("guest", host.room_id)
        clear_sent(host)
        await lost.process.store.commit(Commit(edits=(ParticipantRemoved(host.room_id, guest.peer_id),)))
        generation = lost.generation
        await lost.die()

        await surviving.owner.free(generation)
        await surviving.owner.free(generation)

        assert types_heard(host).count(SignalType.PARTICIPANT_LEFT.value) == 1


class TestTheSweepOfUnheldSeats:
    async def test_a_seat_no_socket_holds_is_freed_after_two_rounds_and_a_held_one_stays(self, pods):
        pod, _ = pods
        unheld = await open_seat(pod.process.store, "lost-reply")
        held = await pod.link.join("held")

        await pod.owner.sweep()
        after_one = await pod.process.store.find_record(unheld)
        await pod.owner.sweep()

        assert after_one is not None
        assert await pod.process.store.find_record(unheld) is None
        assert held.sockets[0].closed_with is None


class TestAStalledProcess:
    async def test_the_sockets_of_a_generation_it_no_longer_holds_close_with_a_server_error(self, pods):
        pod, _ = pods
        browser = await pod.link.join("host")
        pod.process.lease.held_as(Hold(generation="a-newer-generation"))

        await pod.owner.close_sockets_of_old_generations()
        await asyncio.sleep(0)

        assert browser.sockets[0].closed_with == SERVER_ERROR_CLOSE_CODE


class TestAGracefulStop:
    async def test_the_seats_of_the_stopping_process_leave_by_the_lapse_path_and_the_room_hears_once(self, pods):
        stopping, other = pods
        host = await other.link.join("host")
        guest = await stopping.link.join("guest", host.room_id)

        await stopping.owner.free(stopping.generation)

        told = await other.process.store.read_log(host.peer_id, 0)
        assert [entry.frame["type"] for entry in told.frames].count(SignalType.PARTICIPANT_LEFT.value) == 1
        assert await other.process.store.find_record(guest.peer_id) is None
