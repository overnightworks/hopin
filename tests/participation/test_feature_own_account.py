"""An agent with an account of its own, added by a member of the adders group, at HopIn's real `/mcp` and `/ws`."""

import json

import pytest

from hopin.agent.tools import ACCEPT_INVITATION, WAIT
from hopin.participation.vocabulary import AgentRefusal, ParticipationSignal
from tests.support.dialed_call import serving_dialed_call
from tests.support.fake_issuer import FakeIssuer

pytestmark = pytest.mark.integration

OWNER_SUBJECT = "subject-owner"
ADDERS = "/apps/hopin/agent-adders"
AGENT_NAME = "Mozart"
AGENT_USERNAME = "agent-subject-username"
AGENT_SUBJECT = "agent-subject"
OFFERS = ParticipationSignal.AGENT_OFFERS.value
JOINED = ParticipationSignal.AGENT_JOINED.value
REFUSED = ParticipationSignal.AGENT_REFUSED.value


@pytest.fixture
def adding_call(sign_in_settings):
    """HopIn whose adders group is mapped to the owner `OWNER_SUBJECT`."""
    sign_in = sign_in_settings()
    with serving_dialed_call(sign_in, FakeIssuer(sign_in), {OWNER_SUBJECT: ADDERS}) as call:
        yield call


class Scene:
    """The owner in a call, a member of the adders group in it, and the owner's agent that has called once."""

    def __init__(self, call, *, member_groups=(ADDERS,)):
        self.call = call
        self.owner_account, owner_login = call.member(subject=OWNER_SUBJECT, groups=(ADDERS,))
        self.owner = call.seat(owner_login)
        self.member_account, self.member_login = call.member(groups=member_groups)
        self.member = call.seat(self.member_login, self.owner.room_id)
        self.token = call.agent_token(
            owner=OWNER_SUBJECT, subject=AGENT_SUBJECT, agent_name=AGENT_NAME, preferred_username=AGENT_USERNAME
        )
        call.call(self.token, WAIT, {})  # the first call registers the agent

    def key_of_offer(self):
        [offer] = self.member.frame_of_type(OFFERS)["offers"]
        return offer["agent_key"]

    def add(self, key):
        self.member.send({"type": ParticipationSignal.AGENT_ADD.value, "agent_key": key})

    def standings(self):
        return self.call.call(self.token, WAIT, {}).outcome["standings"]

    def accept(self, standing):
        invitation = standing["invitation"]
        arguments = {"room": standing["place"]["id"], "invitation": invitation["id"], "epoch": invitation["epoch"]}
        return self.call.call(self.token, ACCEPT_INVITATION, arguments)


@pytest.fixture
def scene(adding_call):
    return Scene(adding_call)


class TestAddingTheAgent:
    def test_a_member_adds_the_agent_it_accepts_and_every_seat_sees_its_tile_under_its_name(self, scene):
        scene.add(scene.key_of_offer())
        scene.member.frame_of_type(OFFERS)  # the offer, now pending: the invitation is open

        [standing] = scene.standings()
        accepted = scene.accept(standing)

        assert (standing["state"], standing["place"]["id"]) == ("invited", scene.owner.room_id)
        assert accepted.outcome == {"room": scene.owner.room_id, "name": AGENT_NAME}
        joined = scene.owner.frame_of_type(JOINED)
        assert (joined["name"], joined["sponsor_id"]) == (AGENT_NAME, scene.owner.participant_id)
        assert [item["state"] for item in scene.standings()] == ["active"]

    def test_no_frame_a_seat_receives_carries_the_agents_username(self, scene):
        scene.add(scene.key_of_offer())
        scene.member.frame_of_type(OFFERS)
        scene.accept(scene.standings()[0])

        scene.member.frame_of_type(JOINED)

        assert AGENT_USERNAME not in json.dumps(scene.member.received)

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

    def test_a_seat_outside_the_group_is_offered_nothing_and_its_forged_add_is_refused(self, adding_call):
        scene = Scene(adding_call, member_groups=("/apps/hopin",))
        key = scene.owner.frame_of_type(OFFERS)["offers"][0]["agent_key"]

        scene.add(key)

        assert scene.member.frame_of_type(REFUSED)["reason"] == AgentRefusal.NOT_ADDER.value
        assert OFFERS not in [frame["type"] for frame in scene.member.received]
        assert scene.standings() == []

    def test_a_persons_own_agent_cannot_accept_an_invitation(self, scene):
        dialed = scene.call.call(
            scene.call.token(scene.member_account), ACCEPT_INVITATION, {"room": "x", "invitation": "y", "epoch": 0}
        )

        assert (dialed.is_error, dialed.outcome) == (True, {"refused": AgentRefusal.OWN_ACCOUNT_ONLY.value})
