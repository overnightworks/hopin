import pytest

from hopin.agent.tools import JOIN_CALL, LEAVE_CALL
from hopin.participation.vocabulary import AgentRefusal, ParticipationSignal
from tests.support.dialed_call import join_arguments

pytestmark = pytest.mark.integration

NAME = "Claude"


class TestTheTools:
    def test_call_48_join_call_binds_the_agent_to_its_persons_seat_and_the_call_sees_its_tile(self, dialed_call):
        """CALL-48: join_call binds the agent to its person's one seat, and the call is told of its tile."""
        account, _, seat = dialed_call.seated()

        dialed = dialed_call.call(dialed_call.token(account), JOIN_CALL, join_arguments(seat))

        tile = seat.next_frame()
        assert (dialed.is_error, dialed.outcome, tile["type"], tile["name"]) == (
            False,
            {"room": seat.room_id, "name": NAME},
            ParticipationSignal.AGENT_JOINED.value,
            NAME,
        )

    def test_call_48_a_repeat_join_returns_the_same_agent_and_leave_call_takes_it_out(self, dialed_call):
        """CALL-48: A repeat join returns the same agent, and leave_call takes it from the call."""
        account, _, seat = dialed_call.seated()
        token = dialed_call.token(account)

        first = dialed_call.call(token, JOIN_CALL, join_arguments(seat, "First"))
        again = dialed_call.call(token, JOIN_CALL, join_arguments(seat, "Second"))
        left = dialed_call.call(token, LEAVE_CALL, {"room": seat.room_id})

        assert again.outcome == first.outcome
        assert (left.outcome, left.is_error) == ({"left": True}, False)
        assert [seat.next_frame()["type"] for _ in range(2)] == [
            ParticipationSignal.AGENT_JOINED.value,
            ParticipationSignal.AGENT_LEFT.value,
        ]

    @pytest.mark.parametrize(
        ("arguments", "refusal"),
        [
            pytest.param({"room": "no-such-room", "name": NAME}, AgentRefusal.NO_SEAT, id="no seat there"),
            pytest.param({"name": NAME}, AgentRefusal.NO_SEAT, id="no room named"),
            pytest.param({"room": "-", "name": ""}, AgentRefusal.BAD_NAME, id="no name"),
            pytest.param({"room": "-"}, AgentRefusal.BAD_NAME, id="no name given"),
        ],
    )
    def test_a_join_that_cannot_be_served_answers_why(self, dialed_call, arguments, refusal):
        account, _, _ = dialed_call.seated()

        dialed = dialed_call.call(dialed_call.token(account), JOIN_CALL, arguments)

        assert (dialed.is_error, dialed.outcome) == (True, {"refused": refusal.value})

    def test_a_leave_by_room_reaches_only_the_callers_own_agent(self, dialed_call):
        account, _, seat = dialed_call.seated()
        other, _ = dialed_call.member()
        dialed_call.call(dialed_call.token(account), JOIN_CALL, join_arguments(seat))

        dialed = dialed_call.call(dialed_call.token(other), LEAVE_CALL, {"room": seat.room_id})
        again = dialed_call.call(dialed_call.token(account), LEAVE_CALL, {"room": seat.room_id})

        assert (dialed.outcome, again.outcome) == ({"refused": AgentRefusal.GONE.value}, {"left": True})

    def test_a_second_leave_by_room_answers_gone(self, dialed_call):
        account, _, seat = dialed_call.seated()
        token = dialed_call.token(account)
        dialed_call.call(token, JOIN_CALL, join_arguments(seat))

        first = dialed_call.call(token, LEAVE_CALL, {"room": seat.room_id})
        second = dialed_call.call(token, LEAVE_CALL, {"room": seat.room_id})

        assert (first.outcome, second.outcome, second.is_error) == (
            {"left": True},
            {"refused": AgentRefusal.GONE.value},
            True,
        )

    def test_a_token_past_its_life_leaves_the_agent_where_it_is(self, dialed_call):
        account, _, seat = dialed_call.seated()
        dialed_call.call(dialed_call.token(account), JOIN_CALL, join_arguments(seat))

        expired = dialed_call.call(dialed_call.token(account, age_seconds=400), LEAVE_CALL, {"room": seat.room_id})
        fresh = dialed_call.call(dialed_call.token(account), LEAVE_CALL, {"room": seat.room_id})

        assert (expired.status, fresh.outcome) == (401, {"left": True})
