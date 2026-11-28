"""Calls on two `hopin serve` processes over one Redis: a lost process's seats are freed by its lapse, and a call
reaches across the processes."""

import json
import secrets
import time
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from webauth.admission import AppRole
from webauth.ports import AccountKind
from websockets.exceptions import ConnectionClosed

from hopin.config.constants import (
    CALL_LAPSE_SWEEP_SECONDS,
    CALL_LEASE_LASTS_SECONDS,
    CALL_LEASE_RENEWAL_SECONDS,
    HEALTH_PATH,
    HOST_GRACE_SECONDS,
    MAX_LIVE_SEATS_PER_ACCOUNT,
    PERSON_EXPORT_PATH,
    PERSON_PATH,
    SERVER_ERROR_CLOSE_CODE,
    ErrorReason,
    ResumeRefusalCause,
    RoomClosedReason,
    SignalType,
)
from hopin.people import ErasedKind
from hopin.rooms.models import Account
from hopin.signaling.call_store import Commit, ParticipantRemoved, SeatForgotten
from tests.stores.support.call_client import Browser, candidate_to, lock_round_trip, received
from tests.stores.support.call_redis import (
    call_state,
    keys_naming,
    room_record,
    seat_record,
    seats_held,
)
from tests.stores.support.waiting import wait_for
from tests.support.call_state import played_process, seat_in, take_agent_place
from tests.support.people_export import read_archive
from tests.support.signed_in import handshake_headers, seed_member

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.stores.support.call_client import SeatSocket
    from tests.stores.support.pod_pair import Pod
    from tests.support.call_state import Process
    from tests.support.signed_in import SeededMember

pytestmark = [pytest.mark.shared_stores, pytest.mark.integration]

# The lease lapses `lasts` after its last renewal; the sweep that serves the lapse runs every few seconds after.
LAPSE_WAIT_SECONDS = CALL_LEASE_LASTS_SECONDS * 3
STOP_SECONDS = 20
OPEN_ATTEMPTS = 5
# A lapse is served by a sweep, and a process that has swept twice has had its chance to serve one.
SWEEPS_PASSED_SECONDS = CALL_LAPSE_SWEEP_SECONDS * 2 + 1
# One renewal moves a stalled process to its new generation, and the watcher looks once more each renewal.
WATCH_SECONDS = CALL_LEASE_RENEWAL_SECONDS * 2 + 2
# The grace runs out on the clock of the process that timed it.
GRACE_WAIT_SECONDS = HOST_GRACE_SECONDS + 3


@pytest.fixture
def member(pods, postgres_stores, web_auth_config) -> SeededMember:
    return seed_member(postgres_stores, web_auth_config)


@pytest.fixture
def other_member(pods, postgres_stores, web_auth_config) -> SeededMember:
    return seed_member(postgres_stores, web_auth_config)


@pytest.fixture
def third_member(pods, postgres_stores, web_auth_config) -> SeededMember:
    return seed_member(postgres_stores, web_auth_config)


@pytest.fixture
def admin(pods, postgres_stores, web_auth_config) -> SeededMember:
    account = postgres_stores.accounts.create_for_subject(
        f"admin-{secrets.token_hex(4)}", "admin", AppRole.ADMIN, kind=AccountKind.PERSON
    )
    return seed_member(postgres_stores, web_auth_config, account=account)


@pytest.fixture
def seat_on(page_on) -> Callable[[Pod, SeededMember], SeatSocket]:
    def open_seat(pod: Pod, member: SeededMember) -> SeatSocket:
        return page_on(pod, member).open()

    return open_seat


@pytest.fixture
def a_call(page_on) -> Callable[[Pod, SeededMember, Pod, SeededMember], tuple[Browser, Browser]]:
    """A call whose host sits on one pod and whose guest on another."""

    def start(host_pod: Pod, host_member: SeededMember, guest_pod: Pod, guest_member: SeededMember):
        host, guest = page_on(host_pod, host_member), page_on(guest_pod, guest_member)
        host.join()
        guest.join(host.room_id)
        return host, guest

    return start


def opened_once_freed(seat_on, pod: Pod, member: SeededMember) -> SeatSocket:
    """A seat on the pod while it is still freeing a lost process's seats of the account: an open that meets the
    freeing's change fails loud and closes its socket, which the page answers by reconnecting."""
    for _ in range(OPEN_ATTEMPTS):
        seat = seat_on(pod, member)
        try:
            seat.stays_quiet()
        except ConnectionClosed:
            continue
        return seat
    raise AssertionError(f"no seat opened in {OPEN_ATTEMPTS} attempts")


def is_refused_as_too_many(seat: SeatSocket) -> bool:
    return not seat.stays_quiet()


def refusals_of_resume(pod: Pod) -> list[str]:
    return pod.log_lines("resume.refused")


def account_of(member: SeededMember) -> Account:
    return Account(id=member.account.id, name=member.account.username)


def export_seats(pod: Pod, admin: SeededMember, subject: str) -> list[dict]:
    status, body = pod.answer(PERSON_EXPORT_PATH.format(subject=subject), headers=handshake_headers(admin, origin=None))
    assert status == HTTPStatus.OK
    return read_archive(body)[1]["seats"]


def erasure(pod: Pod, admin: SeededMember, subject: str, *, dry_run: bool) -> dict:
    path = PERSON_PATH.format(subject=subject) + ("?dry_run=true" if dry_run else "")
    status, body = pod.answer(path, method="DELETE", headers=handshake_headers(admin))
    assert status == HTTPStatus.OK
    return json.loads(body)


def counted(erased: dict) -> tuple[int, int]:
    """The seats and the calls an erasure counts."""
    return (
        next(entry["count"] for entry in erased["kinds"] if entry["kind"] == ErasedKind.SEAT.value),
        erased["affects_others"][0]["count"],
    )


class TestAProcessThatIsKilled:
    def test_call_42_after_its_lapse_the_other_process_opens_the_eight_seats_it_held_and_refuses_a_ninth(
        self, pods, member, other_member, seat_on, a_call
    ):
        """CALL-42."""
        first, second = pods
        held = [seat_on(first, member) for _ in range(MAX_LIVE_SEATS_PER_ACCOUNT)]
        assert all(seat.stays_quiet() for seat in held)
        assert is_refused_as_too_many(seat_on(second, member))
        _, lapse_witness = a_call(first, other_member, second, other_member)

        first.kill()
        lapse_witness.socket.frame_of_type(SignalType.PARTICIPANT_LEFT.value, LAPSE_WAIT_SECONDS)

        reopened = [opened_once_freed(seat_on, second, member) for _ in range(MAX_LIVE_SEATS_PER_ACCOUNT)]
        assert all(seat.stays_quiet() for seat in reopened)
        assert is_refused_as_too_many(seat_on(second, member))

    def test_call_14_call_01_the_room_of_a_lost_host_is_told_of_it_and_a_call_link_of_it_stays_served(
        self, pods, member, other_member, a_call
    ):
        """CALL-14, CALL-01."""
        first, second = pods
        _, guest = a_call(first, member, second, member)

        first.kill()

        left = guest.socket.frame_of_type(SignalType.PARTICIPANT_LEFT.value, LAPSE_WAIT_SECONDS)
        assert left["type"] == "participant_left"
        assert second.status(HEALTH_PATH) == HTTPStatus.OK

    def test_call_01_the_guests_of_a_lost_host_see_it_gone_and_the_call_ends_after_the_grace_the_survivor_times(
        self, pods, member, other_member, a_call
    ):
        """CALL-01."""
        first, second = pods
        _, guest = a_call(first, member, second, other_member)

        first.kill()
        guest.socket.frame_of_type(SignalType.PARTICIPANT_LEFT.value, LAPSE_WAIT_SECONDS)
        told_at = time.monotonic()
        closed = guest.socket.frame_of_type(SignalType.ROOM_CLOSED.value, GRACE_WAIT_SECONDS + HOST_GRACE_SECONDS)

        assert [frame["type"] for frame in guest.frames if frame["type"] in {"host_away", "participant_left"}] == [
            SignalType.HOST_AWAY.value,
            SignalType.PARTICIPANT_LEFT.value,
        ]
        assert closed["reason"] == RoomClosedReason.HOST_GONE.value
        # The lapse is no end: the room waits for the host the whole grace its survivor times.
        assert time.monotonic() - told_at >= HOST_GRACE_SECONDS - CALL_LAPSE_SWEEP_SECONDS

    def test_a_restarted_process_answers_the_old_link_expired_once_the_old_lapse_is_served_and_starts_a_new_call(
        self, pods, another_pod, page_on, member, other_member, a_call, redis
    ):
        first, second = pods
        host, _ = a_call(first, member, second, other_member)

        first.kill()
        restarted = another_pod()
        wait_for(lambda: seats_held(redis, member.account.id) == 0, LAPSE_WAIT_SECONDS)

        old_link = page_on(restarted, other_member).open().join(host.room_id)
        assert old_link["reason"] == ErrorReason.NOT_FOUND.value
        assert page_on(restarted, member).join()["type"] == SignalType.ROOM_CREATED.value


class TestAProcessThatStartsBesideLiveOnes:
    def test_it_frees_none_of_the_seats_the_live_ones_hold(
        self, pods, another_pod, member, other_member, a_call, seat_on, redis
    ):
        first, second = pods
        _, guest = a_call(first, member, second, other_member)
        assert seat_on(first, member).stays_quiet()
        held = call_state(redis)

        another_pod()
        time.sleep(SWEEPS_PASSED_SECONDS)  # the lease's time is real: the new process has had its sweeps

        guest.reconnect()
        assert (call_state(redis), received(guest, SignalType.PARTICIPANT_LEFT, "participant_id")) == (held, [])

    def test_it_answers_ready_once_it_holds_its_lease_with_no_lapse_to_wait_for(self, pods, another_pod):
        assert another_pod().status(HEALTH_PATH) == HTTPStatus.OK


class TestAStoppedProcess:
    def test_a_graceful_stop_frees_its_seats_at_once_and_tells_the_room_of_the_leaver_once(
        self, pods, member, other_member, a_call, redis
    ):
        first, second = pods
        host, guest = a_call(second, member, first, other_member)

        first.process.terminate()
        first.process.wait(STOP_SECONDS)

        assert seats_held(redis, other_member.account.id) == 0
        host.reconnect()
        assert received(host, SignalType.PARTICIPANT_LEFT, "participant_id") == [guest.peer_id]
        assert (len(first.log_lines("participant.left")), second.log_lines("participant.left")) == (1, [])


class TestAProcessThatStalled:
    def test_the_seat_of_a_lapsed_lease_commits_nothing_and_its_socket_closes_when_the_process_wakes(
        self, pods, member, other_member, a_call, redis
    ):
        first, second = pods
        host, guest = a_call(first, member, second, other_member)

        first.signal_server("STOP")
        guest.socket.frame_of_type(SignalType.PARTICIPANT_LEFT.value, LAPSE_WAIT_SECONDS)
        kept = call_state(redis)
        first.signal_server("CONT")
        host.socket.send({"type": "no_such_frame", "seq": 1})  # a refused frame answers with an error the seat commits

        assert host.socket.close_code(WATCH_SECONDS) == SERVER_ERROR_CLOSE_CODE
        assert call_state(redis) == kept


class TestASeatOfAnotherProcess:
    def test_call_14_before_a_seat_outlives_its_process_a_resume_of_it_on_another_process_is_refused(
        self, pods, member, other_member, a_call, redis
    ):
        first, second = pods
        host, _ = a_call(first, member, second, other_member)

        host.move_to(second)
        refused = host.resume()

        assert refused.frame_of_type(SignalType.ERROR.value)["reason"] == ErrorReason.RESUME_REFUSED.value
        assert [ResumeRefusalCause.UNKNOWN_SEAT.value in line for line in refusals_of_resume(second)] == [True]
        assert seat_record(redis, host.peer_id) is not None

    def test_a_relay_from_a_seat_on_one_process_is_written_to_the_seat_on_the_other_and_a_seat_gone_gets_none(
        self, pods, member, other_member, a_call, redis
    ):
        first, second = pods
        host, guest = a_call(first, member, second, other_member)

        host.say(candidate_to(guest, "across"))
        lock_round_trip(host)
        guest.reconnect()  # the frames of another process reach a seat at its next wake
        assert received(guest, SignalType.ICE_CANDIDATE, "candidate") == ["across"]

        guest.socket.close()
        wait_for(lambda: seat_record(redis, guest.peer_id) is None)
        host.say(candidate_to(guest, "nowhere"))
        host.say({"type": SignalType.UNLOCK_ROOM.value})
        host.socket.frame_of_type(SignalType.ROOM_UNLOCKED.value)
        assert seat_record(redis, guest.peer_id) is None
        assert keys_naming(redis, guest.peer_id) == set()


class TestAJoinAcrossProcesses:
    def test_call_14_a_member_joining_on_the_second_process_a_call_of_the_first_fails_no_work(
        self, pods, other_member, a_call
    ):
        """CALL-14: a seat the other process holds is no work of this one."""
        first, second = pods
        _, guest = a_call(first, other_member, second, other_member)

        guest.socket.until_quiet()  # the frames of the join; the work has run when the socket falls quiet
        for pod in pods:
            assert "feature_work.failed" not in pod.log.read_text()

    def test_call_01_a_host_back_on_the_other_process_keeps_the_call_when_the_grace_its_first_process_timed_ends(
        self, pods, member, other_member, a_call, redis
    ):
        """CALL-01."""
        first, second = pods
        host, guest = a_call(first, member, second, other_member)

        left = time.monotonic()
        host.socket.close()
        wait_for(lambda: (room := room_record(redis, host.room_id)) is not None and room.grace and not room.departures)
        host.move_to(second)
        host.join(host.room_id)
        guest.socket.frame_of_type(SignalType.HOST_BACK.value)
        time.sleep(max(0.0, left + GRACE_WAIT_SECONDS - time.monotonic()))  # the grace's time is real

        lock_round_trip(host)
        guest.socket.until_quiet()
        assert (room := room_record(redis, host.room_id)) is not None
        assert room.grace is None
        assert SignalType.ROOM_CLOSED.value not in [frame["type"] for frame in guest.frames]
        assert [pod.log_lines("room.closed") for pod in pods] == [[], []]


class TestWhatALostProcessLeaves:
    def test_an_agent_place_taken_through_a_process_is_free_after_its_lapse_and_a_stale_release_frees_nothing(
        self, pods, member, other_member, a_call, redis, redis_url
    ):
        first, second = pods
        host, _ = a_call(first, member, second, other_member)

        async def take_and_release_stale(process: Process) -> frozenset[str]:
            await take_agent_place(process.store, host.room_id, "agent")
            await process.store.commit(Commit(room_id=host.room_id, places_released=("an-older-agent",)))
            return (await process.store.get_room(host.room_id)).agent_ids

        lost, kept_by_stale_release = played_process(redis_url, take_and_release_stale)
        assert kept_by_stale_release == {"agent"}

        lost.lose()
        wait_for(lambda: not room_record(redis, host.room_id).agent_ids, LAPSE_WAIT_SECONDS)
        assert keys_naming(redis, lost.generation) == set()

    def test_a_departure_its_process_never_announced_is_told_once_at_the_lapse_and_counts_nowhere(
        self, pods, member, other_member, third_member, admin, a_call, page_on, seat_on, redis, redis_url
    ):
        first, second = pods
        host, guest = a_call(first, member, second, other_member)

        async def depart_untold(process: Process) -> str:
            peer_id = await seat_in(process.store, host.room_id, "leaver", account_of(third_member))
            await process.store.commit(
                Commit(edits=(SeatForgotten(peer_id), ParticipantRemoved(host.room_id, peer_id)))
            )
            return peer_id

        lost, leaver = played_process(redis_url, depart_untold)

        assert seats_held(redis, third_member.account.id) == 0
        assert export_seats(first, admin, third_member.account.id) == []
        ghost = page_on(first, third_member)
        ghost.peer_id, ghost.resume_token = leaver, "a-token"
        assert ghost.resume().frame_of_type(SignalType.ERROR.value)["reason"] == ErrorReason.RESUME_REFUSED.value
        assert [ResumeRefusalCause.UNKNOWN_SEAT.value in line for line in refusals_of_resume(first)] == [True]
        assert all(seat_on(second, third_member).stays_quiet() for _ in range(MAX_LIVE_SEATS_PER_ACCOUNT))

        lost.lose()
        wait_for(lambda: not room_record(redis, host.room_id).departures, LAPSE_WAIT_SECONDS)
        host.reconnect()
        guest.reconnect()
        assert [received(page, SignalType.PARTICIPANT_LEFT, "participant_id") for page in (host, guest)] == [
            [leaver],
            [leaver],
        ]


class TestPersonDataAcrossProcesses:
    def test_export_dry_run_and_erase_count_and_remove_the_seats_and_hosted_calls_of_both_processes(
        self, pods, member, other_member, admin, a_call, seat_on, redis
    ):
        first, second = pods
        subject = member.account.id
        host, _ = a_call(first, member, second, other_member)
        assert seat_on(second, member).stays_quiet()
        for pod in pods:
            seats = export_seats(pod, admin, subject)
            assert sorted((seat["host"], seat["call_id"]) for seat in seats) == [(False, None), (True, host.room_id)]
        held = call_state(redis)

        dry_run = erasure(second, admin, subject, dry_run=True)
        assert (counted(dry_run), call_state(redis)) == ((2, 1), held)

        erased = erasure(first, admin, subject, dry_run=False)
        assert counted(erased) == (2, 1)
        assert [seats_held(redis, account) for account in (subject, other_member.account.id)] == [0, 0]
        assert room_record(redis, host.room_id) is None
