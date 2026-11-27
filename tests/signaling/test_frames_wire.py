import pytest

from hopin.config.constants import ErrorReason, SignalType
from hopin.participation.frames import AgentOffersFrame, Offer
from hopin.signaling.frames import (
    ErrorFrame,
    ExistingParticipantsFrame,
    JsonObject,
    MediaStateBroadcastFrame,
    OfferRelayFrame,
    PeerId,
    WireFrame,
)


@pytest.mark.parametrize(
    ("frame", "sent"),
    [
        pytest.param(
            ErrorFrame(reason=ErrorReason.FULL, message="m"),
            {"type": SignalType.ERROR.value, "reason": ErrorReason.FULL.value, "message": "m"},
            id="a defaulted field that is None is omitted and an enum is sent by its value",
        ),
        pytest.param(
            ErrorFrame(reason=ErrorReason.FULL, message="m", capacity=2),
            {"type": SignalType.ERROR.value, "reason": ErrorReason.FULL.value, "message": "m", "capacity": 2},
            id="a defaulted field that is set is sent in field order",
        ),
        pytest.param(
            OfferRelayFrame(sdp="v=0", candidate=None, from_=PeerId("p1")),
            {"type": SignalType.OFFER.value, "sdp": "v=0", "candidate": None, "from": "p1"},
            id="a field without a default is sent as null",
        ),
        pytest.param(
            MediaStateBroadcastFrame(from_=PeerId("p1"), audio=True, video=False),
            {"type": SignalType.MEDIA_STATE.value, "from": "p1", "audio": True, "video": False},
            id="the sender goes out under its wire key",
        ),
        pytest.param(
            AgentOffersFrame(shown=True, full=False, offers=[Offer("k", "Bach", pending=True)], send_away=["a1"]),
            {
                "type": "agent_offers",
                "shown": True,
                "full": False,
                "offers": [{"agent_key": "k", "name": "Bach", "pending": True}],
                "send_away": ["a1"],
            },
            id="a nested record is sent as its own object",
        ),
    ],
)
def test_a_frame_sends_its_fields_in_order_under_the_wire_rules(frame: WireFrame, sent: JsonObject) -> None:
    assert list(frame.to_json().items()) == list(sent.items())


def test_a_frame_sends_copies_of_its_containers() -> None:
    names = {"p1": "Ana"}
    sent = ExistingParticipantsFrame(
        participants=["p1"], names=names, is_host=True, locked=False, participant_id=PeerId("p1"), resume_token="t"
    ).to_json()

    names["p2"] = "Bo"

    assert sent["names"] == {"p1": "Ana"}
