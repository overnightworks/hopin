import pytest

from hopin.agent.tools import JOIN_CALL, LEAVE_CALL
from hopin.participation.vocabulary import AgentRefusal
from tests.support.dialed_call import join_arguments

pytestmark = pytest.mark.integration

WAIT = "wait"


class TestAnAgentWithItsOwnAccount:
    def test_its_wait_answers_at_once_with_its_empty_standing(self, dialed_call):
        dialed = dialed_call.call(dialed_call.agent_token(owner="owner-subject"), WAIT, {})

        assert (dialed.is_error, dialed.outcome["standings"], bool(dialed.outcome["cursor"])) == (False, [], True)

    def test_a_wait_with_a_cursor_from_another_run_answers_at_once(self, dialed_call):
        token = dialed_call.agent_token(owner="owner-subject")

        dialed = dialed_call.call(token, WAIT, {"after": "a-run-before-the-restart-7", "timeout_seconds": 50})

        assert (dialed.is_error, dialed.outcome["standings"]) == (False, [])
        assert dialed.outcome["cursor"] != "a-run-before-the-restart-7"

    def test_its_join_call_is_refused_invitation_only_and_puts_no_tile_on_the_stage(self, dialed_call):
        account, _, seat = dialed_call.seated()

        dialed = dialed_call.call(dialed_call.agent_token(owner=account.id), JOIN_CALL, join_arguments(seat))

        assert (dialed.is_error, dialed.outcome) == (True, {"refused": AgentRefusal.INVITATION_ONLY.value})
        seat.settle()  # the first frame after the call is the answer to this one, so no tile was announced

    def test_its_leave_call_by_room_answers_gone(self, dialed_call):
        _, _, seat = dialed_call.seated()

        dialed = dialed_call.call(dialed_call.agent_token(owner="owner-subject"), LEAVE_CALL, {"room": seat.room_id})

        assert (dialed.is_error, dialed.outcome) == (True, {"refused": AgentRefusal.GONE.value})


class TestAPersonsOwnAgent:
    def test_its_wait_is_refused_own_account_only(self, dialed_call):
        account, _ = dialed_call.member()

        dialed = dialed_call.call(dialed_call.token(account), WAIT, {})

        assert (dialed.is_error, dialed.outcome) == (True, {"refused": AgentRefusal.OWN_ACCOUNT_ONLY.value})
