"""The hearing socket of a seat, opened as that seat's browser opens it, for the tests of the hearing route."""

from hopin.participation.vocabulary import HEARING_PATH
from tests.support.signed_in import handshake_headers

SOUND = bytes(100)


def hop(scene, *, seat=None, login=...):
    """The hearing socket of a seat, opened as that seat's browser opens it; the owner's seat and login by default, and
    no login at all for `None`."""
    seat = scene.owner if seat is None else seat
    return scene.call.client.websocket_connect(
        f"{HEARING_PATH}/{seat.participant_id}", headers=handshake_headers(scene.owner_login if login is ... else login)
    )


def closed_with(socket):
    message = socket.receive()
    assert message["type"] == "websocket.close"
    return message["code"]
