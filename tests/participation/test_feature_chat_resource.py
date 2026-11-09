"""The chat window as a resource at HopIn's real `/mcp` and `/ws`."""

import json

import pytest
from agent_runtime_client.member import LEASE_SECONDS, STANDING_URI
from mcp_types import INVALID_PARAMS

from hopin.agent.tools import JOIN_CALL, LEAVE_CALL, SAY
from hopin.config.constants import SignalType
from hopin.participation.vocabulary import ParticipationSignal
from tests.support.dialed_call import join_arguments, window_uri

pytestmark = pytest.mark.integration

LONE_SURROGATE = "\ud800"
OFFERS = ParticipationSignal.AGENT_OFFERS.value
OFFER_UPDATES = 3  # the agent joined, left and invited: the offers a member is sent are told again each time


def chat(message):
    return {"type": SignalType.CHAT.value, "message": message}


def window_of(answer):
    """The window a successful read answered, as its JSON."""
    return json.loads(answer["result"]["contents"][0]["text"])


def is_unknown_resource(answer):
    return answer.get("error", {}).get("code") == INVALID_PARAMS


@pytest.fixture
def own_agent(dialed_call):
    """A person's own agent, joined in its person's call: the person's account, its token and its seat."""
    account, _, seat = dialed_call.seated()
    token = dialed_call.token(account)
    dialed_call.call(token, JOIN_CALL, join_arguments(seat))
    return account, token, seat


@pytest.fixture
def joined(scene):
    """The scene with the owner's agent, which has an account of its own, accepted."""
    scene.invite()
    scene.accept(scene.standings()[0])
    return scene


class TestListingTheWindows:
    def test_call_54_an_agent_with_an_account_of_its_own_lists_its_standing_and_its_window(self, joined):
        assert set(joined.call.listed(joined.token)) == {STANDING_URI, window_uri(joined.owner.room_id)}

    def test_call_54_a_persons_own_agent_lists_its_window_alone(self, dialed_call, own_agent):
        _, token, seat = own_agent

        assert dialed_call.listed(token) == [window_uri(seat.room_id)]

    def test_call_54_a_person_holding_two_places_lists_two_windows(self, dialed_call):
        account, login, first = dialed_call.seated()
        second = dialed_call.seat(login)
        token = dialed_call.token(account)
        for seat in (first, second):
            dialed_call.call(token, JOIN_CALL, join_arguments(seat))

        assert dialed_call.listed(token) == sorted(window_uri(seat.room_id) for seat in (first, second))


class TestReadingAWindow:
    def test_call_54_a_read_holds_the_lines_said_after_the_join_with_their_names_and_own_marks(
        self, dialed_call, own_agent
    ):
        """CALL-54: The read answers the window with seq, kind, name, text and own."""
        _, token, seat = own_agent
        other = dialed_call.seat(dialed_call.member()[1], seat.room_id)
        other.send(chat("hello"))
        seat.frame_of_type(SignalType.CHAT.value)
        dialed_call.call(token, SAY, {"room": seat.room_id, "text": "hi back"})

        window = window_of(dialed_call.window(token, seat.room_id))

        assert [(line["seq"], line["kind"], line["text"], line["own"]) for line in window["entries"]] == [
            (1, "person", "hello", False),
            (2, "agent", "hi back", True),
        ]
        assert window["starts_at"] == 1

    def test_call_54_a_line_that_cannot_be_read_takes_no_seq_and_the_read_still_answers(self, dialed_call, own_agent):
        """CALL-54: An empty line or one that does not encode as UTF-8 is not held and takes no seq."""
        _, token, seat = own_agent
        other = dialed_call.seat(dialed_call.member()[1], seat.room_id)
        other.send(chat(f"broken {LONE_SURROGATE}"))
        other.send(chat("fine"))
        while seat.frame_of_type(SignalType.CHAT.value)["message"] != "fine":
            pass

        window = window_of(dialed_call.window(token, seat.room_id))

        assert [(line["seq"], line["text"]) for line in window["entries"]] == [(1, "fine")]


class TestWhatIsNotReadable:
    def test_call_54_another_principals_room_is_an_unknown_resource(self, dialed_call, own_agent):
        _, _, seat = own_agent
        stranger = dialed_call.token(dialed_call.member()[0])

        assert is_unknown_resource(dialed_call.window(stranger, seat.room_id))

    def test_call_54_the_room_of_an_invited_agent_is_an_unknown_resource(self, scene):
        scene.invite()

        assert is_unknown_resource(scene.call.window(scene.token, scene.owner.room_id))

    def test_call_54_a_room_the_agent_left_is_an_unknown_resource(self, dialed_call, own_agent):
        _, token, seat = own_agent
        dialed_call.call(token, LEAVE_CALL, {"room": seat.room_id})

        assert is_unknown_resource(dialed_call.window(token, seat.room_id))

    def test_call_54_a_room_that_ended_is_an_unknown_resource(self, dialed_call, own_agent):
        _, token, seat = own_agent
        seat.send({"type": SignalType.END_ROOM.value})
        seat.frame_of_type(SignalType.ROOM_CLOSED.value)

        assert is_unknown_resource(dialed_call.window(token, seat.room_id))

    def test_call_54_a_lease_that_ran_out_takes_the_window_with_it(self, joined):
        joined.call.pass_time(LEASE_SECONDS)

        assert is_unknown_resource(joined.call.window(joined.token, joined.owner.room_id))

    def test_call_54_the_sponsor_of_an_agent_with_an_account_of_its_own_reads_nothing_through_it(self, scene):
        """CALL-54: A place belongs to the person whose agent it is, never to the person who sponsors it."""
        account = scene.owner_account
        own_token = scene.call.token(account)
        scene.call.call(own_token, JOIN_CALL, join_arguments(scene.owner))
        scene.call.call(own_token, LEAVE_CALL, {"room": scene.owner.room_id})
        scene.add(scene.key_of_offer())
        for _ in range(OFFER_UPDATES):
            if any(offer["pending"] for offer in scene.member.frame_of_type(OFFERS)["offers"]):
                break
        scene.accept(scene.standings()[0])

        answers = [scene.call.window(token, scene.owner.room_id) for token in (own_token, scene.token)]

        assert [is_unknown_resource(answer) for answer in answers] == [True, False]
