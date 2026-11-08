"""An agent with an account of its own, added by a member of the adders group, at HopIn's real `/mcp` and `/ws`."""

import json

import pytest
from agent_runtime_client.member import ACCEPT_INVITATION_TOOL, STANDING_URI, PlaceRef, place_window_uri

from hopin.participation.vocabulary import AgentRefusal, ParticipationSignal
from tests.support.dialed_call import accept_arguments

pytestmark = pytest.mark.integration

OFFERS = ParticipationSignal.AGENT_OFFERS.value
JOINED = ParticipationSignal.AGENT_JOINED.value
REFUSED = ParticipationSignal.AGENT_REFUSED.value


class TestAddingTheAgent:
    def test_a_member_adds_the_agent_it_accepts_and_every_seat_sees_its_tile_under_its_name(self, scene):
        scene.invite()

        [standing] = scene.standings()
        accepted = scene.accept(standing)

        assert (standing["state"], standing["place"]["id"]) == ("invited", scene.owner.room_id)
        assert accepted.outcome == {"room": scene.owner.room_id, "name": scene.agent_name}
        joined = scene.owner.frame_of_type(JOINED)
        assert (joined["name"], joined["sponsor_id"]) == (scene.agent_name, scene.owner.participant_id)
        assert [item["state"] for item in scene.standings()] == ["active"]

    def test_no_frame_a_seat_receives_carries_the_agents_username(self, scene):
        scene.invite()
        scene.accept(scene.standings()[0])

        scene.member.frame_of_type(JOINED)

        assert scene.agent_username not in json.dumps(scene.member.received)

    def test_an_add_of_a_member_the_issuer_no_longer_holds_in_the_group_is_not_adder(self, scene):
        key = scene.key_of_offer()
        scene.call.issuer.grant_groups(scene.member_account.id, ())

        scene.add(key)

        assert scene.member.frame_of_type(REFUSED)["reason"] == AgentRefusal.NOT_ADDER.value
        assert scene.standings() == []

    def test_an_add_while_the_issuer_refuses_the_groups_is_unavailable(self, scene):
        key = scene.key_of_offer()
        scene.call.issuer.refuses_refresh_grants = True

        scene.add(key)

        assert scene.member.frame_of_type(REFUSED)["reason"] == AgentRefusal.UNAVAILABLE.value

    def test_a_seat_outside_the_group_is_offered_nothing_and_its_forged_add_is_refused(self, scene_of):
        scene = scene_of(member_groups=("/apps/hopin",))
        key = scene.owner.frame_of_type(OFFERS)["offers"][0]["agent_key"]

        scene.add(key)

        assert scene.member.frame_of_type(REFUSED)["reason"] == AgentRefusal.NOT_ADDER.value
        assert OFFERS not in [frame["type"] for frame in scene.member.received]
        assert scene.standings() == []

    def test_a_persons_own_agent_is_refused_own_account_only_whatever_it_sends(self, scene):
        dialed = scene.call.call(scene.call.token(scene.member_account), ACCEPT_INVITATION_TOOL, {})

        assert (dialed.is_error, dialed.outcome) == (True, {"refused": AgentRefusal.OWN_ACCOUNT_ONLY.value})


STALE_ARGUMENTS = {
    "another_place_kind": lambda valid: {**valid, "place": {**valid["place"], "kind": "quiz"}},
    "a_missing_field": lambda valid: {key: value for key, value in valid.items() if key != "invitation"},
    "an_extra_field": lambda valid: {**valid, "room": valid["place"]["id"]},
    "a_bool_epoch": lambda valid: {**valid, "epoch": True},
    "a_negative_epoch": lambda valid: {**valid, "epoch": -1},
    "another_invitation_id": lambda valid: {**valid, "invitation": "another"},
    "another_epoch": lambda valid: {**valid, "epoch": valid["epoch"] + 1},
}


class TestAcceptingInTheSharedShape:
    @pytest.mark.parametrize("stale", STALE_ARGUMENTS.values(), ids=STALE_ARGUMENTS.keys())
    def test_arguments_that_do_not_match_the_invitation_answer_stale_invitation_and_leave_it_open(self, scene, stale):
        scene.invite()
        [standing] = scene.standings()

        dialed = scene.call.call(scene.token, ACCEPT_INVITATION_TOOL, stale(accept_arguments(standing)))

        assert (dialed.is_error, dialed.outcome) == (True, {"refused": AgentRefusal.STALE_INVITATION.value})
        assert [item["state"] for item in scene.standings()] == ["invited"]


FOREIGN_URIS = {
    "a_place_window": place_window_uri(PlaceRef(kind="call", id="room")),
    "a_foreign_uri": "member://elsewhere",
    "a_non_canonical_spelling": f"{STANDING_URI}/",
}


class TestTheStandingResource:
    def test_the_agent_lists_exactly_the_standing_and_reads_its_invited_place(self, scene):
        scene.invite()

        listed = scene.call.request(scene.token, "resources/list").json()["result"]["resources"]

        assert [resource["uri"] for resource in listed] == [STANDING_URI]
        [standing] = scene.standings()
        assert (standing["state"], standing["place"]) == ("invited", {"kind": "call", "id": scene.owner.room_id})

    def test_the_standing_of_an_agent_whose_owners_seat_is_parked_reads_as_paused(self, scene):
        scene.invite()
        [standing] = scene.standings()
        scene.accept(standing)

        scene.owner.drop()

        assert [item["state"] for item in scene.standings()] == ["paused"]

    def test_another_agents_read_shows_none_of_its_places(self, scene):
        scene.invite()
        other = scene.call.agent_token(owner="another-owner", subject="another-agent")

        assert scene.call.standing(other) == []

    @pytest.mark.parametrize("uri", FOREIGN_URIS.values(), ids=FOREIGN_URIS.keys())
    def test_any_other_uri_answers_the_unknown_resource_error_and_echoes_nothing(self, scene, uri):
        answer = scene.call.request(scene.token, "resources/read", {"uri": uri}, routing_name=uri)

        error = answer.json()["error"]
        assert (error["message"], uri in answer.text) == ("Unknown resource", False)
