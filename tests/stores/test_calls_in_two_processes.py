"""Calls on two `hopin serve` processes over one Redis: a lost process's seats are freed by its lapse, and a call
reaches across the processes."""

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from websockets.exceptions import ConnectionClosed

from hopin.config.constants import (
    CALL_LEASE_LASTS_SECONDS,
    HEALTH_PATH,
    MAX_LIVE_SEATS_PER_ACCOUNT,
    SignalType,
)
from tests.stores.support.call_client import SeatSocket
from tests.support.signed_in import seed_member

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.stores.support.pod_pair import Pod
    from tests.support.signed_in import SeededMember

pytestmark = [pytest.mark.shared_stores, pytest.mark.integration]

# The lease lapses `lasts` after its last renewal; the sweep that serves the lapse runs every few seconds after.
LAPSE_WAIT_SECONDS = CALL_LEASE_LASTS_SECONDS * 3
OPEN_ATTEMPTS = 5


@pytest.fixture
def member(pods, postgres_stores, web_auth_config) -> SeededMember:
    return seed_member(postgres_stores, web_auth_config)


@pytest.fixture
def other_member(pods, postgres_stores, web_auth_config) -> SeededMember:
    return seed_member(postgres_stores, web_auth_config)


@pytest.fixture
def sockets() -> Iterator[list[SeatSocket]]:
    opened: list[SeatSocket] = []
    yield opened
    for seat in opened:
        seat.close()


def opened(sockets: list[SeatSocket], pod: Pod, member: SeededMember) -> SeatSocket:
    seat = SeatSocket(pod, member)
    sockets.append(seat)
    return seat


def a_guest_of_a_host_on(first: Pod, second: Pod, member: SeededMember, sockets: list[SeatSocket]) -> SeatSocket:
    """A guest, on the second process, of a call whose host sits on the first."""
    room_id = opened(sockets, first, member).join()["room_id"]
    guest = opened(sockets, second, member)
    guest.join(room_id)
    return guest


def opened_once_freed(sockets: list[SeatSocket], pod: Pod, member: SeededMember) -> SeatSocket:
    """A seat on the pod while it is still freeing a lost process's seats of the account: an open that meets the
    freeing's change fails loud and closes its socket, which the page answers by reconnecting."""
    for _ in range(OPEN_ATTEMPTS):
        seat = opened(sockets, pod, member)
        try:
            seat.stays_quiet()
        except ConnectionClosed:
            continue
        return seat
    raise AssertionError(f"no seat opened in {OPEN_ATTEMPTS} attempts")


def is_refused_as_too_many(seat: SeatSocket) -> bool:
    return not seat.stays_quiet()


class TestAProcessThatIsKilled:
    def test_call_42_after_its_lapse_the_other_process_opens_the_eight_seats_it_held_and_refuses_a_ninth(
        self, pods, member, other_member, sockets
    ):
        """CALL-42."""
        first, second = pods
        held = [opened(sockets, first, member) for _ in range(MAX_LIVE_SEATS_PER_ACCOUNT)]
        assert all(seat.stays_quiet() for seat in held)
        assert is_refused_as_too_many(opened(sockets, second, member))
        lapse_witness = a_guest_of_a_host_on(first, second, other_member, sockets)

        first.kill()
        lapse_witness.frame_of_type(SignalType.PARTICIPANT_LEFT.value, LAPSE_WAIT_SECONDS)

        reopened = [opened_once_freed(sockets, second, member) for _ in range(MAX_LIVE_SEATS_PER_ACCOUNT)]
        assert all(seat.stays_quiet() for seat in reopened)
        assert is_refused_as_too_many(opened(sockets, second, member))

    def test_call_14_call_01_the_room_of_a_lost_host_is_told_of_it_and_a_call_link_of_it_stays_served(
        self, pods, member, other_member, sockets
    ):
        """CALL-14, CALL-01."""
        first, second = pods
        guest = a_guest_of_a_host_on(first, second, member, sockets)

        first.kill()

        assert guest.frame_of_type(SignalType.PARTICIPANT_LEFT.value, LAPSE_WAIT_SECONDS)["type"] == "participant_left"
        assert second.status(HEALTH_PATH) == HTTPStatus.OK
