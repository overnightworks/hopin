import pytest
from agent_runtime_client.mcp_server import listen
from agent_runtime_client.member import LEASE_SECONDS, STANDING_URI

from hopin.agent.tools import JOIN_CALL, LEAVE_CALL
from tests.participation.support.own_accounts import AGENT, agent_calls
from tests.support.dialed_call import join_arguments

pytestmark = pytest.mark.integration

ANOTHER_OWNER = "another-owner-subject"

RENEWING_REQUESTS = {
    "a_read": lambda call, token: call.standing(token),
    "a_list": lambda call, token: call.request(token, "resources/list"),
    "a_listen": lambda call, token: call.request(
        token, "subscriptions/listen", {"notifications": {"resourceSubscriptions": [STANDING_URI]}}
    ),
}


@pytest.fixture(autouse=True)
def short_listens(monkeypatch):
    """A listen ends at once, so the request that opens it returns."""
    monkeypatch.setattr(listen, "LISTEN_BOUND_SECONDS", 0)


@pytest.fixture
def joined(scene):
    """The scene with the agent accepted, so it holds an active place."""
    scene.invite()
    [standing] = scene.standings()
    scene.accept(standing)
    return scene


class TestTheLease:
    def test_a_lease_that_runs_out_ends_the_places_so_the_next_read_finds_none(self, joined):
        scene = joined

        scene.call.pass_time(LEASE_SECONDS)

        assert scene.standings() == []

    @pytest.mark.parametrize("renew", RENEWING_REQUESTS.values(), ids=RENEWING_REQUESTS.keys())
    def test_a_request_within_the_lease_renews_it_so_the_place_outlives_the_first_deadline(self, joined, renew):
        scene = joined
        scene.call.pass_time(LEASE_SECONDS - 1)
        renew(scene.call, scene.token)

        scene.call.pass_time(LEASE_SECONDS - 1)

        assert [item["state"] for item in scene.standings()] == ["active"]

    def test_a_call_with_another_owner_ends_the_old_registration_and_its_places(self, joined):
        scene = joined
        other_owner = scene.call.agent_token(owner=ANOTHER_OWNER, subject="agent-subject")

        assert scene.call.standing(other_owner) == []

    def test_a_call_after_the_deadline_but_before_the_timer_ran_ends_the_old_lease(self, joined):
        scene = joined
        scene.call.scheduler.skip(LEASE_SECONDS)

        assert scene.standings() == []

    def test_a_persons_own_agent_holds_no_lease(self, dialed_call):
        account, _, seat = dialed_call.seated()
        dialed_call.call(dialed_call.token(account), JOIN_CALL, join_arguments(seat))

        dialed_call.pass_time(LEASE_SECONDS)

        left = dialed_call.call(dialed_call.token(account), LEAVE_CALL, {"room": seat.room_id})
        assert left.outcome == {"left": True}


@pytest.mark.asyncio
class TestWhoIsLive:
    async def test_an_agent_is_live_from_its_first_call_until_its_lease_runs_out(self, participation, scheduler):
        before = participation.is_live_agent(AGENT)
        await agent_calls(participation)
        during = participation.is_live_agent(AGENT)

        await scheduler.advance(LEASE_SECONDS)

        assert (before, during, participation.is_live_agent(AGENT)) == (False, True, False)
