"""An agent's framing of its figure at HopIn's real `/mcp` and `/ws`: taken, told to every seat and kept for a seat
that joins later."""

import pytest
from agents.member import SET_FRAMING_TOOL

from hopin.agent.tools import LEAVE_CALL
from hopin.participation.vocabulary import ParticipationSignal
from tests.participation.support.figure_scene import JOINED, accept_with, offered_tools
from tests.participation.support.tool_calls import call_in

pytestmark = pytest.mark.integration

FRAMED = ParticipationSignal.AGENT_FRAMING.value


class TestTheFramingTool:
    def test_call_77_an_agent_with_a_shown_figure_is_offered_the_framing_tool_until_it_leaves(self, gesturing):
        offered = SET_FRAMING_TOOL in offered_tools(gesturing)
        gesturing.call.call(gesturing.token, LEAVE_CALL, {"room": gesturing.owner.room_id})

        assert (offered, SET_FRAMING_TOOL in offered_tools(gesturing)) == (True, False)

    def test_call_77_an_agent_without_a_figure_is_not_offered_it(self, scene):
        accept_with(scene, None)

        assert SET_FRAMING_TOOL not in offered_tools(scene)

    @pytest.mark.parametrize("framing", ["face", "shoulders", "upperBody", "fullBody"])
    def test_call_77_a_framing_is_taken_and_every_seat_is_told_it_with_the_agent_s_id(self, gesturing, framing):
        framed = call_in(gesturing, tool=SET_FRAMING_TOOL, framing=framing)

        assert (framed.is_error, framed.outcome) == (False, {})
        for seat in (gesturing.owner, gesturing.member):
            told = seat.frame_of_type(FRAMED)
            assert (told["agent_id"], told["framing"]) == (gesturing.agent_id, framing)

    def test_call_77_a_framing_set_again_tells_no_seat_twice(self, gesturing):
        call_in(gesturing, tool=SET_FRAMING_TOOL, framing="face")
        gesturing.member.frame_of_type(FRAMED)

        again = call_in(gesturing, tool=SET_FRAMING_TOOL, framing="face")
        call_in(gesturing, tool=SET_FRAMING_TOOL, framing="fullBody")

        assert (again.is_error, again.outcome) == (False, {})
        assert gesturing.member.frame_of_type(FRAMED)["framing"] == "fullBody"

    def test_call_78_a_seat_that_joins_later_is_told_the_kept_framing_with_the_agent_s_tile(self, gesturing):
        call_in(gesturing, tool=SET_FRAMING_TOOL, framing="upperBody")
        _, later_login = gesturing.call.member()
        later = gesturing.call.seat(later_login, gesturing.owner.room_id)

        assert later.frame_of_type(JOINED)["framing"] == "upperBody"

    def test_call_78_the_framing_stands_through_a_pause_of_the_agent_s_place(self, gesturing, seat_changes):
        call_in(gesturing, tool=SET_FRAMING_TOOL, framing="face")
        seat_changes.clear()
        gesturing.owner.drop()
        assert seat_changes.wait(timeout=10)
        _, later_login = gesturing.call.member()
        later = gesturing.call.seat(later_login, gesturing.owner.room_id)

        assert later.frame_of_type(JOINED)["framing"] == "face"

    def test_call_78_a_tile_told_before_any_framing_carries_none(self, gesturing):
        _, later_login = gesturing.call.member()
        later = gesturing.call.seat(later_login, gesturing.owner.room_id)

        assert "framing" not in later.frame_of_type(JOINED)

    @pytest.mark.parametrize(
        "arguments",
        [
            pytest.param({"framing": "face", "place": {"kind": "call", "id": "no-such-room"}}, id="another room"),
            pytest.param({"framing": "face", "place": {"kind": "table", "id": "any"}}, id="a place of another kind"),
        ],
    )
    def test_call_77_a_framing_at_no_call_the_agent_holds_is_not_active(self, gesturing, arguments):
        dialed = gesturing.call.call(gesturing.token, SET_FRAMING_TOOL, arguments)

        assert (dialed.is_error, dialed.outcome) == (True, {"refused": "not_active"})

    def test_call_77_a_framing_while_the_sponsors_seat_is_parked_is_not_active_and_tells_no_seat(
        self, gesturing, seat_changes
    ):
        seat_changes.clear()
        gesturing.owner.drop()
        assert seat_changes.wait(timeout=10)

        dialed = call_in(gesturing, tool=SET_FRAMING_TOOL, framing="face")

        assert (dialed.is_error, dialed.outcome) == (True, {"refused": "not_active"})
        assert FRAMED not in [frame["type"] for frame in gesturing.member.received]

    def test_call_77_a_framing_the_schema_does_not_name_fails_the_schema(self, gesturing):
        dialed = call_in(gesturing, tool=SET_FRAMING_TOOL, framing="closeup")

        assert dialed.is_error
