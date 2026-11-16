"""Every member-tool call HopIn serves through `/mcp` writes one `tool_call.ended` line with its outcome."""

import pytest
from agents.mcp_server.asgi import LOGGER_NAME
from agents.member import ACCEPT_INVITATION_TOOL
from logline import Outcome

from tests.support.log_lines import captured_lines

pytestmark = pytest.mark.integration

TOOL_CALL_LINE = "tool_call.ended"


class TestTheToolCallLine:
    def test_a_tool_call_writes_one_line_naming_the_tool_and_its_outcome(self, scene):
        scene.invite()
        [standing] = scene.standings()

        with captured_lines(LOGGER_NAME) as lines:
            accepted = scene.accept(standing)

        [line] = [line for line in lines if line["event"] == TOOL_CALL_LINE]
        assert accepted.status == 200
        assert (line["tool_name"], line["outcome"], isinstance(line["duration_ms"], int | float)) == (
            ACCEPT_INVITATION_TOOL,
            Outcome.OK,
            True,
        )
