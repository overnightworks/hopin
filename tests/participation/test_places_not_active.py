"""CALL-60, CALL-63: a tool that names a place the agent holds no joined place in is refused as not_active, whatever the
tool, at HopIn's real `/mcp`."""

import pytest
from agents.member import SPEAK_TOOL

from hopin.agent.tools import CALL_KIND, LOOK
from tests.support.agent_speak import TEXT

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("tool", "named"),
    [pytest.param(SPEAK_TOOL, {"text": TEXT}, id="speak"), pytest.param(LOOK, {}, id="look")],
)
@pytest.mark.parametrize(
    "place",
    [
        pytest.param({"place": {"kind": "elsewhere", "id": "x"}}, id="another-kind-of-place"),
        pytest.param({"place": {"kind": CALL_KIND, "id": "no-such-call"}}, id="a-call-it-is-not-in"),
        pytest.param({}, id="no-place"),
    ],
)
def test_call_60_63_a_place_the_agent_holds_no_joined_place_in_is_not_active(joined_agent, tool, named, place):
    dialed = joined_agent.call.call(joined_agent.token, tool, {**place, **named})

    assert (dialed.is_error, dialed.outcome) == (True, {"refused": "not_active"})
