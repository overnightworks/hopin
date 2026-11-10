import pytest
from agents.mcp_wire import PROTOCOL_VERSION_HEADER
from agents.member import ACCEPT_INVITATION_TOOL, STANDING_URI
from mcp_types import UNSUPPORTED_PROTOCOL_VERSION

from hopin.agent.tools import JOIN_CALL, LEAVE_CALL, SAY
from hopin.participation.vocabulary import AgentRefusal
from tests.support.dialed_call import join_arguments

pytestmark = pytest.mark.integration

OWNER = "owner-subject"


class TestAnAgentWithItsOwnAccount:
    def test_its_first_read_answers_its_empty_standing(self, dialed_call):
        assert dialed_call.standing(dialed_call.agent_token(owner=OWNER)) == []

    def test_its_join_call_is_refused_invitation_only_and_puts_no_tile_on_the_stage(self, dialed_call):
        account, _, seat = dialed_call.seated()

        dialed = dialed_call.call(dialed_call.agent_token(owner=account.id), JOIN_CALL, join_arguments(seat))

        assert (dialed.is_error, dialed.outcome) == (True, {"refused": AgentRefusal.INVITATION_ONLY.value})
        seat.settle()  # the first frame after the call is the answer to this one, so no tile was announced

    def test_its_leave_call_by_room_answers_gone(self, dialed_call):
        _, _, seat = dialed_call.seated()

        dialed = dialed_call.call(dialed_call.agent_token(owner=OWNER), LEAVE_CALL, {"room": seat.room_id})

        assert (dialed.is_error, dialed.outcome) == (True, {"refused": AgentRefusal.GONE.value})


class TestAPersonsOwnAgent:
    def test_it_lists_no_resource_and_reads_no_standing(self, dialed_call):
        account, _ = dialed_call.member()
        token = dialed_call.token(account)

        listed = dialed_call.request(token, "resources/list").json()["result"]["resources"]
        read = dialed_call.request(token, "resources/read", {"uri": STANDING_URI}, routing_name=STANDING_URI).json()

        assert (listed, read["error"]["message"]) == ([], "Unknown resource")

    def test_it_joins_says_and_leaves_on_the_same_revision(self, dialed_call):
        account, _, seat = dialed_call.seated()
        token = dialed_call.token(account)

        joined = dialed_call.call(token, JOIN_CALL, join_arguments(seat))
        said = dialed_call.call(token, SAY, {"room": seat.room_id, "text": "hello"})
        left = dialed_call.call(token, LEAVE_CALL, {"room": seat.room_id})

        assert [dialed.is_error for dialed in (joined, said, left)] == [False, False, False]


class TestTheEndpoint:
    def test_tools_list_holds_the_four_tools_and_no_wait(self, dialed_call):
        token = dialed_call.token(dialed_call.member()[0])

        tools = dialed_call.request(token, "tools/list").json()["result"]["tools"]

        assert {tool["name"] for tool in tools} == {JOIN_CALL, ACCEPT_INVITATION_TOOL, LEAVE_CALL, SAY}

    def test_a_call_of_wait_is_an_unknown_tool(self, dialed_call):
        token = dialed_call.agent_token(owner=OWNER)

        dialed = dialed_call.request(token, "tools/call", {"name": "wait", "arguments": {}}, routing_name="wait")

        assert "Unknown tool" in dialed.json()["error"]["message"]

    @pytest.mark.parametrize("version", [None, "2025-11-25"])
    def test_a_request_without_the_current_revision_answers_the_unsupported_version_error(self, dialed_call, version):
        token = dialed_call.token(dialed_call.member()[0])

        answer = dialed_call.request(token, "tools/list", **{PROTOCOL_VERSION_HEADER: version})

        assert answer.json()["error"]["code"] == UNSUPPORTED_PROTOCOL_VERSION

    def test_initialize_answers_the_unsupported_version_error(self, dialed_call):
        token = dialed_call.token(dialed_call.member()[0])

        answer = dialed_call.request(token, "initialize")

        assert answer.json()["error"]["code"] == UNSUPPORTED_PROTOCOL_VERSION
