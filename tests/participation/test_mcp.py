import pytest
from starlette.websockets import WebSocketDisconnect
from webauth.admission import AppRole

from hopin.agent.mcp import MCP_GROUP, MCP_SCOPE
from hopin.config.constants import CALL_FEATURES_PATH, LOGIN_GROUP, POLICY_VIOLATION_CLOSE_CODE, WEBSOCKET_PATH
from hopin.participation.vocabulary import AgentRefusal, ParticipationSignal
from tests.participation.dialed_call import METADATA_PATH, admin_groups
from tests.support.signed_in import handshake_headers

pytestmark = pytest.mark.integration

JOIN = "join_call"
LEAVE = "leave_call"
NAME = "Claude"


def seated(dialed_call):
    """A person in a call of their own, with their account for a token."""
    account, member = dialed_call.member()
    seat = dialed_call.seat(member)
    return account, member, seat


def join_arguments(seat, name=NAME):
    return {"room": seat.room_id, "name": name}


class TestWhoIsLetIn:
    @pytest.mark.parametrize(
        ("token_options", "status", "challenged"),
        [
            pytest.param({"no_bearer": True}, 401, True, id="no bearer"),
            pytest.param({"audience": "https://elsewhere.test.invalid/mcp"}, 401, True, id="a wrong audience"),
            pytest.param({"authorized_party": "hopin"}, 401, True, id="a foreign azp"),
            pytest.param({"scope": "openid"}, 403, True, id="no mcp scope"),
            pytest.param({"groups": (LOGIN_GROUP,)}, 403, False, id="a person outside the mcp group"),
            pytest.param({"age_seconds": 400}, 401, True, id="a token past its life and the leeway"),
        ],
    )
    def test_a_token_the_gate_refuses_runs_no_tool_and_carries_the_challenge_where_one_helps(
        self, dialed_call, token_options, status, challenged
    ):
        account, _, seat = seated(dialed_call)
        no_bearer = token_options.pop("no_bearer", False)
        token = None if no_bearer else dialed_call.token(account, **token_options)

        dialed = dialed_call.call(token, JOIN, join_arguments(seat))

        assert dialed.status == status
        assert ("resource_metadata" in dialed.response.headers.get("www-authenticate", "")) is challenged
        assert dialed_call.call(dialed_call.token(account), LEAVE, {"agent_id": "none"}).outcome == {
            "refused": AgentRefusal.GONE.value
        }

    def test_a_person_with_no_account_is_refused(self, dialed_call):
        stranger = dialed_call.issuer.access_token(
            "nobody-signed-in",
            audience=dialed_call.audience,
            authorized_party="agents",
            scope=MCP_SCOPE,
            groups=(LOGIN_GROUP, MCP_GROUP),
        )

        assert dialed_call.call(stranger, JOIN, {"room": "x", "name": NAME}).status == 403

    @pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
    def test_a_request_that_is_no_post_is_refused_with_a_valid_token(self, dialed_call, method):
        account, _, _ = seated(dialed_call)

        assert dialed_call.mcp(method, dialed_call.token(account)).status_code == 405

    def test_a_post_that_carries_an_origin_is_refused(self, dialed_call):
        account, _, seat = seated(dialed_call)

        dialed = dialed_call.call(
            dialed_call.token(account), JOIN, join_arguments(seat), origin="https://page.test.invalid"
        )

        assert dialed.status == 403

    def test_the_metadata_names_the_resource_without_a_session(self, dialed_call):
        document = dialed_call.client.get(METADATA_PATH)

        assert (document.status_code, document.json()["scopes_supported"]) == (200, [MCP_SCOPE])

    def test_the_agents_token_opens_neither_the_pages_api_nor_the_socket(self, dialed_call):
        account, _, _ = seated(dialed_call)
        token = dialed_call.token(account)

        refused_api = dialed_call.client.get(CALL_FEATURES_PATH, headers={"authorization": f"Bearer {token}"})
        with (
            pytest.raises(WebSocketDisconnect) as refused_socket,
            dialed_call.client.websocket_connect(
                WEBSOCKET_PATH, headers={**handshake_headers(None), "authorization": f"Bearer {token}"}
            ),
        ):
            pass

        assert refused_api.status_code in {302, 303, 401}
        assert refused_socket.value.code == POLICY_VIOLATION_CLOSE_CODE

    def test_an_admins_token_is_admitted_as_a_user_and_joins_like_any_person(self, dialed_call):
        account, member = dialed_call.member(role=AppRole.ADMIN)
        seat = dialed_call.seat(member)

        dialed = dialed_call.call(dialed_call.token(account, groups=admin_groups()), JOIN, join_arguments(seat))

        assert (dialed.status, dialed.is_error) == (200, False)


class TestTheTools:
    def test_call_48_join_call_binds_the_agent_to_its_persons_seat_and_the_call_sees_its_tile(self, dialed_call):
        """CALL-48: join_call binds the agent to its person's one seat, and the call is told of its tile."""
        account, _, seat = seated(dialed_call)

        dialed = dialed_call.call(dialed_call.token(account), JOIN, join_arguments(seat))

        tile = seat.next_frame()
        assert (dialed.is_error, tile["type"], tile["agent_id"], tile["name"]) == (
            False,
            ParticipationSignal.AGENT_JOINED.value,
            dialed.outcome["agent_id"],
            NAME,
        )

    def test_call_48_a_repeat_join_returns_the_same_agent_and_leave_call_takes_it_out(self, dialed_call):
        """CALL-48: A repeat join returns the same agent, and leave_call takes it from the call."""
        account, _, seat = seated(dialed_call)
        token = dialed_call.token(account)

        first = dialed_call.call(token, JOIN, join_arguments(seat, "First"))
        again = dialed_call.call(token, JOIN, join_arguments(seat, "Second"))
        left = dialed_call.call(token, LEAVE, {"agent_id": first.outcome["agent_id"]})

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
        account, _, _ = seated(dialed_call)

        dialed = dialed_call.call(dialed_call.token(account), JOIN, arguments)

        assert (dialed.is_error, dialed.outcome) == (True, {"refused": refusal.value})

    def test_another_persons_agent_id_is_not_yours(self, dialed_call):
        account, _, seat = seated(dialed_call)
        other, _ = dialed_call.member()
        agent_id = dialed_call.call(dialed_call.token(account), JOIN, join_arguments(seat)).outcome["agent_id"]

        dialed = dialed_call.call(dialed_call.token(other), LEAVE, {"agent_id": agent_id})

        assert dialed.outcome == {"refused": AgentRefusal.NOT_YOURS.value}

    def test_a_token_past_its_life_leaves_the_agent_where_it_is(self, dialed_call):
        account, _, seat = seated(dialed_call)
        agent_id = dialed_call.call(dialed_call.token(account), JOIN, join_arguments(seat)).outcome["agent_id"]

        expired = dialed_call.call(dialed_call.token(account, age_seconds=400), LEAVE, {"agent_id": agent_id})
        fresh = dialed_call.call(dialed_call.token(account), LEAVE, {"agent_id": agent_id})

        assert (expired.status, fresh.outcome) == (401, {"left": True})
