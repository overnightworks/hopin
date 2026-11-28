"""A call on one `hopin serve` over a real Redis: the call's journey through the process, what the store keeps of it,
and what the process does when its Redis or its own lease goes."""

import time
from functools import partial
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest

from hopin.config.constants import (
    CALL_LEASE_LASTS_SECONDS,
    CALL_LEASE_RENEWAL_SECONDS,
    HEALTH_PATH,
    REPLAY_LOG_LIMIT,
    SERVER_ERROR_CLOSE_CODE,
    ErrorReason,
    SignalType,
)
from tests.stores.support.call_client import Browser, candidate_to, heard, lock_round_trip, received
from tests.stores.support.call_redis import call_keys, everything_stored
from tests.stores.support.waiting import wait_for
from tests.support.signed_in import seed_member

if TYPE_CHECKING:
    from tests.support.signed_in import SeededMember

pytestmark = [pytest.mark.shared_stores, pytest.mark.integration]

STOP_SECONDS = 20
# A commit that meets a Redis that does not answer is retried before it fails.
REDIS_REFUSAL_SECONDS = 30
# One renewal moves the lease to its new generation, and the watcher looks once more each renewal.
WATCH_SECONDS = CALL_LEASE_RENEWAL_SECONDS * 2 + 2
STALL_SECONDS = CALL_LEASE_LASTS_SECONDS + 3


@pytest.fixture
def member(pod, postgres_stores, web_auth_config) -> SeededMember:
    return seed_member(postgres_stores, web_auth_config)


@pytest.fixture
def other_member(pod, postgres_stores, web_auth_config) -> SeededMember:
    return seed_member(postgres_stores, web_auth_config)


@pytest.fixture
def page_of(pod, page_on):
    return partial(page_on, pod)


@pytest.fixture
def host_and_guest(page_of, member, other_member) -> tuple[Browser, Browser]:
    host, guest = page_of(member), page_of(other_member)
    host.join()
    guest.join(host.room_id)
    host.socket.frame_of_type(SignalType.PARTICIPANT_JOINED.value)
    return host, guest


class TestAStoppedProcess:
    def test_a_graceful_stop_frees_the_seats_at_once_and_tells_the_room_of_the_first_leaver_once(
        self, pod, host_and_guest, redis
    ):
        assert call_keys(redis)

        pod.process.terminate()
        pod.process.wait(STOP_SECONDS)

        # The second seat to leave takes the room with it, so only the first leaver is announced.
        told = [line for line in pod.log.read_text().splitlines() if "participant.left" in line]
        assert len(told) == 1
        assert call_keys(redis) == set()


class TestWhatTheStoreKeeps:
    def test_no_key_and_no_log_line_holds_a_resume_token_and_a_wrong_account_is_refused(
        self, pod, host_and_guest, page_of, member, redis
    ):
        host, guest = host_and_guest
        tokens = {host.resume_token, guest.resume_token}
        guest.socket.drop()
        guest.resume()
        tokens.add(guest.resumed()["resume_token"])
        assert len(tokens) == 3

        stored = everything_stored(redis) + pod.log.read_bytes()
        assert [token for token in tokens if token.encode() in stored] == []

        thief = page_of(member)
        thief.open()
        thief.socket.send(guest.resume_frame())
        assert thief.socket.frame_of_type(SignalType.ERROR.value)["reason"] == ErrorReason.RESUME_REFUSED.value


class TestTheCallThroughOneProcess:
    def test_call_10_a_description_and_candidates_reach_the_other_seat_in_order_both_ways(self, host_and_guest):
        host, guest = host_and_guest

        host.say({"type": SignalType.OFFER.value, "target": guest.peer_id, "sdp": "offer"})
        for index in range(3):
            host.say(candidate_to(guest, f"to-guest-{index}"))
        guest.say({"type": SignalType.ANSWER.value, "target": host.peer_id, "sdp": "answer"})
        for index in range(3):
            guest.say(candidate_to(host, f"to-host-{index}"))

        assert guest.socket.frame_of_type(SignalType.OFFER.value)["sdp"] == "offer"
        assert [frame["candidate"] for frame in heard(guest, SignalType.ICE_CANDIDATE, 3)] == [
            f"to-guest-{index}" for index in range(3)
        ]
        assert host.socket.frame_of_type(SignalType.ANSWER.value)["sdp"] == "answer"
        assert [frame["candidate"] for frame in heard(host, SignalType.ICE_CANDIDATE, 3)] == [
            f"to-host-{index}" for index in range(3)
        ]

    def test_call_10_a_seat_that_resumes_within_its_window_is_replayed_what_it_missed_and_nobody_saw_it_leave(
        self, host_and_guest
    ):
        host, guest = host_and_guest
        guest.socket.drop()
        host.say(candidate_to(guest, "while-away-1"))
        host.say(candidate_to(guest, "while-away-2"))
        lock_round_trip(host)

        guest.resume()
        guest.resumed()
        guest.socket.frame_of_type(SignalType.ROOM_LOCKED.value)

        assert received(guest, SignalType.ICE_CANDIDATE, "candidate") == ["while-away-1", "while-away-2"]
        assert received(host, SignalType.PARTICIPANT_LEFT, "participant_id") == []

    def test_a_description_that_a_newer_one_superseded_is_not_replayed(self, host_and_guest):
        host, guest = host_and_guest
        guest.socket.drop()
        for sdp in ("first", "second"):
            host.say({"type": SignalType.OFFER.value, "target": guest.peer_id, "sdp": sdp})
        lock_round_trip(host)

        guest.resume()
        guest.resumed()
        guest.socket.frame_of_type(SignalType.ROOM_LOCKED.value)

        assert received(guest, SignalType.OFFER, "sdp") == ["second"]

    def test_a_resume_that_asks_for_frames_before_the_log_floor_is_refused_and_the_seat_leaves(self, host_and_guest):
        host, guest = host_and_guest
        guest.socket.drop()
        for index in range(REPLAY_LOG_LIMIT + 5):
            host.say(candidate_to(guest, f"candidate-{index}"))
        lock_round_trip(host)

        guest.resume(last_seq=0)

        assert guest.socket.frame_of_type(SignalType.ERROR.value)["reason"] == ErrorReason.RESUME_REFUSED.value
        assert host.socket.frame_of_type(SignalType.PARTICIPANT_LEFT.value)["participant_id"] == guest.peer_id

    def test_call_13_the_same_account_in_the_call_twice_stays_host_while_one_connection_is_left(
        self, host_and_guest, page_of, member
    ):
        host, guest = host_and_guest
        second = page_of(member)
        second.join(host.room_id)
        guest.socket.frame_of_type(SignalType.PARTICIPANT_JOINED.value)

        host.socket.close()
        guest.socket.frame_of_type(SignalType.PARTICIPANT_LEFT.value)
        second.say({"type": SignalType.LOCK_ROOM.value})

        assert guest.socket.frame_of_type(SignalType.ROOM_LOCKED.value)
        assert SignalType.HOST_AWAY.value not in [frame["type"] for frame in guest.socket.frames]

    def test_a_call_locks_loses_a_guest_sees_its_host_away_and_back_and_is_ended_by_it(
        self, host_and_guest, page_of, other_member, redis
    ):
        host, guest = host_and_guest
        witness = page_of(other_member)
        witness.join(host.room_id)
        host.socket.frame_of_type(SignalType.PARTICIPANT_JOINED.value)

        host.say({"type": SignalType.LOCK_ROOM.value})
        witness.socket.frame_of_type(SignalType.ROOM_LOCKED.value)
        guest.socket.close()
        host.socket.frame_of_type(SignalType.PARTICIPANT_LEFT.value)
        host.socket.close()
        witness.socket.frame_of_type(SignalType.HOST_AWAY.value)
        host.join(host.room_id)
        witness.socket.frame_of_type(SignalType.HOST_BACK.value)
        host.say({"type": SignalType.END_ROOM.value})

        witness.socket.frame_of_type(SignalType.ROOM_CLOSED.value)
        wait_for(lambda: call_keys(redis) == set())


class TestARedisThatDoesNotAnswer:
    @pytest.fixture
    def refusing_redis(self, host_and_guest, redis, redis_gate) -> set[bytes]:
        """The call's keys as they stood when this process's Redis stopped answering."""
        stood = call_keys(redis)
        redis_gate.close()
        return stood

    def test_a_call_act_fails_loud_with_the_server_error_close(self, host_and_guest, refusing_redis):
        host, _ = host_and_guest

        host.say({"type": SignalType.LOCK_ROOM.value})

        assert host.socket.close_code(REDIS_REFUSAL_SECONDS) == SERVER_ERROR_CLOSE_CODE

    def test_the_process_answers_not_ready(self, pod, refusing_redis):
        wait_for(lambda: pod.status(HEALTH_PATH) == HTTPStatus.SERVICE_UNAVAILABLE)

    def test_a_seat_that_could_not_open_leaves_no_key_behind(self, page_of, other_member, refusing_redis, redis):
        newcomer = page_of(other_member)
        with pytest.raises(Exception, match=r"."):
            newcomer.join()

        assert call_keys(redis) == refusing_redis


class TestARedisThatLostItsData:
    def test_the_sockets_close_with_the_server_error_within_a_renewal_and_a_new_call_starts(
        self, host_and_guest, page_of, member, redis
    ):
        host, guest = host_and_guest

        redis.flushdb()

        assert [page.socket.close_code(WATCH_SECONDS) for page in (host, guest)] == [SERVER_ERROR_CLOSE_CODE] * 2
        assert page_of(member).join()["type"] == SignalType.ROOM_CREATED.value


class TestAProcessThatStalled:
    def test_the_sockets_of_its_old_generation_close_with_the_server_error_and_the_pages_reconnect(
        self, pod, host_and_guest, page_of, member
    ):
        host, guest = host_and_guest

        pod.signal_server("STOP")
        time.sleep(STALL_SECONDS)  # the lease's time is real: it lapses `lasts` after its last renewal
        pod.signal_server("CONT")

        assert [page.socket.close_code(WATCH_SECONDS) for page in (host, guest)] == [SERVER_ERROR_CLOSE_CODE] * 2
        assert page_of(member).join()["type"] == SignalType.ROOM_CREATED.value
