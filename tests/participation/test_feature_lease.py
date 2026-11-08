from concurrent.futures import ThreadPoolExecutor

import pytest
from agent_runtime_client.wait import LEASE_SECONDS

from hopin.agent.tools import JOIN_CALL, LEAVE_CALL
from tests.support.dialed_call import join_arguments

pytestmark = pytest.mark.integration

WAIT = "wait"
OWNER = "owner-subject"
SHORT_WAIT = {"timeout_seconds": 1}
PENDING_CHECK_SECONDS = 0.2


def cursor_of(dialed):
    return dialed.outcome["cursor"]


@pytest.fixture
def agent(dialed_call):
    """An agent with an account of its own that has called once: its token and the cursor `wait` gave it."""
    token = dialed_call.agent_token(owner=OWNER)
    return token, cursor_of(dialed_call.call(token, WAIT, {}))


class TestTheLease:
    def test_a_pending_wait_answers_with_a_new_cursor_when_the_lease_times_out(self, dialed_call, agent):
        token, cursor = agent
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(dialed_call.call, token, WAIT, {"after": cursor})
            with pytest.raises(TimeoutError):
                pending.result(timeout=PENDING_CHECK_SECONDS)

            dialed_call.pass_time(LEASE_SECONDS)

            assert cursor_of(pending.result()) != cursor

    def test_a_call_within_the_lease_renews_it_so_the_wait_stays_blocked_past_the_first_deadline(
        self, dialed_call, agent
    ):
        token, cursor = agent
        dialed_call.pass_time(LEASE_SECONDS - 1)
        dialed_call.call(token, WAIT, {})

        dialed_call.pass_time(LEASE_SECONDS - 1)

        assert cursor_of(dialed_call.call(token, WAIT, {"after": cursor, **SHORT_WAIT})) == cursor

    def test_a_call_with_another_owner_answers_a_wait_at_once_with_a_new_cursor(self, dialed_call, agent):
        _, cursor = agent
        other_owner = dialed_call.agent_token(owner="another-owner-subject")

        dialed_call.call(other_owner, WAIT, {})

        assert cursor_of(dialed_call.call(other_owner, WAIT, {"after": cursor})) != cursor

    def test_a_call_after_the_deadline_but_before_the_timer_ran_ends_the_old_lease(self, dialed_call, agent):
        token, cursor = agent
        dialed_call.scheduler.skip(LEASE_SECONDS)

        dialed_call.call(token, WAIT, {})

        assert cursor_of(dialed_call.call(token, WAIT, {"after": cursor})) != cursor

    def test_a_persons_own_agent_holds_no_lease(self, dialed_call):
        account, _, seat = dialed_call.seated()
        dialed_call.call(dialed_call.token(account), JOIN_CALL, join_arguments(seat))

        dialed_call.pass_time(LEASE_SECONDS)

        left = dialed_call.call(dialed_call.token(account), LEAVE_CALL, {"room": seat.room_id})
        assert left.outcome == {"left": True}
