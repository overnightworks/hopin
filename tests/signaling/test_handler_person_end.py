"""What the handler counts of one person and ends for them when the person is erased."""

import pytest
import pytest_asyncio

from hopin.config.constants import SESSION_ENDED_CLOSE_CODE, ErrorReason, RoomClosedReason, SignalType
from hopin.signaling.handler import PersonReach
from tests.signaling.support.browsers import Link, host_and_guest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def link(handler):
    link = Link(handler)
    yield link
    await link.close_all()


class TestWhatAPersonHolds:
    async def test_a_guest_seat_counts_as_a_seat_and_no_call(self, link, handler):
        await host_and_guest(link)

        assert handler.reach_of("guest") == PersonReach(seats=1, calls=0)

    async def test_a_hosted_call_with_a_guest_counts_as_a_call(self, link, handler):
        await host_and_guest(link)

        assert handler.reach_of("host") == PersonReach(seats=1, calls=1)

    async def test_a_hosted_call_with_a_joined_agent_counts_as_a_call(self, link, handler, room_manager):
        host = await link.join("host")
        room_manager.add_agent(host.room_id, "agent-seat")

        assert handler.reach_of("host") == PersonReach(seats=1, calls=1)

    async def test_a_host_alone_in_a_call_changes_nothing_for_others(self, link, handler):
        await link.join("host")

        assert handler.reach_of("host") == PersonReach(seats=1, calls=0)

    async def test_a_person_with_no_seat_holds_nothing(self, link, handler):
        await host_and_guest(link)

        assert handler.reach_of("stranger") == PersonReach(seats=0, calls=0)

    async def test_counting_changes_nothing(self, link, handler, room_manager):
        host, guest = await host_and_guest(link)

        handler.reach_of("host")

        assert room_manager.find_room(host.room_id) is not None
        assert guest.socket.closed_with is None
        assert host.socket.closed_with is None


class TestEndingWhatAPersonHolds:
    async def test_a_guest_seat_is_closed_as_a_session_end_and_the_room_hears_it_left(self, link, handler):
        host, guest = await host_and_guest(link)

        reach = await handler.end_all_of("guest")

        assert reach == PersonReach(seats=1, calls=0)
        assert guest.socket.closed_with == SESSION_ENDED_CLOSE_CODE
        assert [frame["participant_id"] for frame in host.frames(SignalType.PARTICIPANT_LEFT)] == [guest.peer_id]
        assert host.socket.closed_with is None

    async def test_a_hosted_call_ends_at_once_for_everyone_without_the_hosts_name(self, link, handler, room_manager):
        host, guest = await host_and_guest(link)

        reach = await handler.end_all_of("host")

        assert reach == PersonReach(seats=1, calls=1)
        [closed] = guest.frames(SignalType.ROOM_CLOSED)
        assert (closed["reason"], closed["host_name"]) == (RoomClosedReason.ENDED.value, "")
        assert room_manager.find_room(host.room_id) is None
        assert guest.socket.closed_with is not None

    async def test_a_call_whose_host_is_in_grace_ends_at_once_and_counts(self, link, handler, room_manager):
        host, guest = await host_and_guest(link)
        await host.drop(1000)
        assert handler.reach_of("host") == PersonReach(seats=0, calls=1)

        reach = await handler.end_all_of("host")

        assert reach == PersonReach(seats=0, calls=1)
        assert [frame["reason"] for frame in guest.frames(SignalType.ROOM_CLOSED)] == [RoomClosedReason.ENDED.value]
        assert room_manager.find_room(host.room_id) is None

    async def test_a_host_alone_in_grace_loses_the_call_and_changes_nothing_for_others(
        self, link, handler, room_manager
    ):
        host = await link.join("host")
        await host.drop(1000)

        reach = await handler.end_all_of("host")

        assert reach == PersonReach(seats=0, calls=0)
        assert room_manager.find_room(host.room_id) is None

    async def test_a_parked_seat_is_gone_and_cannot_be_resumed(self, link, handler):
        host, guest = await host_and_guest(link)
        await guest.drop()

        reach = await handler.end_all_of("guest")
        await guest.resume()

        assert reach == PersonReach(seats=1, calls=0)
        assert [frame["participant_id"] for frame in host.frames(SignalType.PARTICIPANT_LEFT)] == [guest.peer_id]
        assert guest.frames(SignalType.RESUMED) == []
        assert guest.frames(SignalType.ERROR)[0]["reason"] == ErrorReason.RESUME_REFUSED.value

    async def test_a_person_with_no_seat_ends_nothing(self, link, handler, room_manager):
        host, guest = await host_and_guest(link)

        reach = await handler.end_all_of("stranger")

        assert reach == PersonReach(seats=0, calls=0)
        assert room_manager.find_room(host.room_id) is not None
        assert guest.socket.closed_with is None

    async def test_every_call_the_person_hosts_ends(self, link, handler, room_manager):
        first, second = await link.join("host"), await link.join("host")
        guest = await link.join("guest", first.room_id)

        reach = await handler.end_all_of("host")

        assert reach == PersonReach(seats=2, calls=1)
        assert [room_manager.find_room(room_id) for room_id in (first.room_id, second.room_id)] == [None, None]
        assert guest.frames(SignalType.ROOM_CLOSED) != []

    async def test_a_second_end_finds_nothing(self, link, handler):
        await host_and_guest(link)
        await handler.end_all_of("host")

        assert await handler.end_all_of("host") == PersonReach(seats=0, calls=0)
