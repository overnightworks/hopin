from hopin.participation.memberships import Membership, SeatViews
from hopin.participation.offers import Offers
from hopin.participation.participants import DialedAgent
from hopin.participation.places import Places
from hopin.signaling.frames import PeerId
from hopin.signaling.transition import RoomView
from tests.participation.support.own_accounts import AGENT, OWNER

ROOM = "room"
SENDER = PeerId("sender")
OWNERS_SEAT = PeerId("owners-seat")


def offers_with_the_agent_joined():
    places = Places()
    agent = DialedAgent("a1", "Claude", AGENT, OWNERS_SEAT, ROOM, owner=OWNER)
    places.put(AGENT, ROOM, agent)
    return Offers(places, lambda person: True, lambda key: False, SeatViews()), agent


def view_of(*, hosts):
    return RoomView(
        room_id=ROOM,
        members=(SENDER, OWNERS_SEAT),
        hosts=frozenset(hosts),
        accounts={SENDER: "sender-account", OWNERS_SEAT: OWNER},
    )


def test_call_84_a_failed_read_decides_on_the_facts_of_the_call_so_a_seat_that_is_host_by_then_is_not_refused():
    offers, agent = offers_with_the_agent_joined()

    decided = offers.after_send_away_read(view_of(hosts={SENDER}), SENDER, agent, Membership.FAILED)

    assert decided is agent
