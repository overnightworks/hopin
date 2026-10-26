import pytest

from hopin.signaling.routing import RoomRouting


def test_seats_cannot_be_reassigned_on_the_routing(room_manager, scheduler):
    routing = RoomRouting(room_manager, scheduler)

    with pytest.raises(AttributeError):
        routing.seats = object()
