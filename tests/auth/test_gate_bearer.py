import pytest
from starlette.websockets import WebSocketDisconnect
from webauth.admission import AppRole

from hopin.agent.mcp import MCP_GROUP, MCP_SCOPE
from hopin.agent.tools import JOIN_CALL, LEAVE_CALL
from hopin.config.constants import CALL_FEATURES_PATH, LOGIN_GROUP, POLICY_VIOLATION_CLOSE_CODE, WEBSOCKET_PATH
from hopin.participation.vocabulary import AgentRefusal
from tests.support.dialed_call import METADATA_PATH, admin_groups, join_arguments
from tests.support.signed_in import handshake_headers

pytestmark = pytest.mark.integration

NAME = "Claude"


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
        account, _, seat = dialed_call.seated()
        no_bearer = token_options.pop("no_bearer", False)
        token = None if no_bearer else dialed_call.token(account, **token_options)

        dialed = dialed_call.call(token, JOIN_CALL, join_arguments(seat))

        assert dialed.status == status
        assert ("resource_metadata" in dialed.response.headers.get("www-authenticate", "")) is challenged
        assert dialed_call.call(dialed_call.token(account), LEAVE_CALL, {"agent_id": "none"}).outcome == {
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

        assert dialed_call.call(stranger, JOIN_CALL, {"room": "x", "name": NAME}).status == 403

    @pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
    def test_a_request_that_is_no_post_is_refused_with_a_valid_token(self, dialed_call, method):
        account, _, _ = dialed_call.seated()

        assert dialed_call.mcp(method, dialed_call.token(account)).status_code == 405

    def test_a_post_that_carries_an_origin_is_refused(self, dialed_call):
        account, _, seat = dialed_call.seated()

        dialed = dialed_call.call(
            dialed_call.token(account), JOIN_CALL, join_arguments(seat), origin="https://page.test.invalid"
        )

        assert dialed.status == 403

    def test_the_metadata_names_the_resource_without_a_session(self, dialed_call):
        document = dialed_call.client.get(METADATA_PATH)

        assert (document.status_code, document.json()["scopes_supported"]) == (200, [MCP_SCOPE])

    def test_the_agents_token_opens_neither_the_pages_api_nor_the_socket(self, dialed_call):
        account, _, _ = dialed_call.seated()
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

        dialed = dialed_call.call(dialed_call.token(account, groups=admin_groups()), JOIN_CALL, join_arguments(seat))

        assert (dialed.status, dialed.is_error) == (200, False)

    def test_a_token_of_an_agents_own_account_is_admitted_as_an_agent_and_join_call_answers_no_seat(self, dialed_call):
        _, _, seat = dialed_call.seated()

        dialed = dialed_call.call(dialed_call.agent_token(), JOIN_CALL, join_arguments(seat))

        assert (dialed.status, dialed.outcome) == (200, {"refused": AgentRefusal.NO_SEAT.value})
        seat.settle()  # the first frame after the call is the answer to this one, so no tile was announced
