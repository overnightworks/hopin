"""CALL-52: an accept or a decline whose arguments fail validation is a tool error naming the invalid fields."""

import json

import pytest
from agents.member import ACCEPT_INVITATION_TOOL, DECLINE_INVITATION_TOOL

from hopin.agent.tools import _accepted, _declined
from hopin.participation.participants import Caller, DialedAgent, Person

pytestmark = pytest.mark.asyncio

SECRET = "secret-value"
CALLER = Caller(Person("issuer", "agent"))
VALID = {"place": {"kind": "call", "id": "room"}, "invitation": "invitation-1", "epoch": 3}


class _StandingInvitation:
    """The participation a call reaches when its arguments are valid: the invitation stands and is taken."""

    def __init__(self):
        self.taken = []

    async def accept(self, person, room_id, invitation, epoch, figure, voices):
        self.taken.append(invitation)
        return _joined(room_id)

    async def decline(self, person, room_id, invitation, epoch, outcome):
        self.taken.append(invitation)
        return


def _joined(room_id):
    return DialedAgent(agent_id="agent", name="agent", person=CALLER.person, sponsor="seat", room_id=room_id)


TOOLS = {
    ACCEPT_INVITATION_TOOL: (_accepted, VALID),
    DECLINE_INVITATION_TOOL: (_declined, VALID | {"reason": "busy"}),
}


@pytest.mark.parametrize("tool", TOOLS, ids=["accept", "decline"])
class TestAMalformedInvitationCall:
    async def test_it_answers_a_tool_error_naming_the_field_paths_and_no_value_or_refusal_code(self, tool):
        call, valid = TOOLS[tool]
        participation = _StandingInvitation()
        malformed = {**valid, "epoch": SECRET, "place": {"kind": "call", "id": 5}, "voice": SECRET}

        answered = await call(participation, CALLER, malformed)

        assert answered.is_error
        assert "epoch" in answered.content
        assert "place.id" in answered.content
        assert "voice" in answered.content
        assert SECRET not in answered.content
        assert "refused" not in answered.content
        assert "stale_invitation" not in answered.content
        assert participation.taken == []

    async def test_a_correct_call_after_it_still_succeeds(self, tool):
        call, valid = TOOLS[tool]
        participation = _StandingInvitation()

        await call(participation, CALLER, {**valid, "epoch": SECRET})
        answered = await call(participation, CALLER, valid)

        assert not answered.is_error
        assert participation.taken == [valid["invitation"]]
        assert json.loads(answered.content) is not None
