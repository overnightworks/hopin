import pytest

from hopin.config.constants import ErrorReason, SignalType
from hopin.participation.frames import AgentOffersFrame, Offer
from hopin.signaling.frames import (
    ChatBroadcastFrame,
    ErrorFrame,
    ExistingParticipantsFrame,
    JsonObject,
    PeerId,
    WireFrame,
)
from hopin.skat.frames import EmptyTableFrame


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
            EmptyTableFrame(revision=4, attachment=None, table=None, src=None, opener_id=None),
            {"type": "skat_table", "revision": 4, "attachment": None, "table": None, "src": None, "opener_id": None},
            id="a field without a default is sent as null",
        ),
        pytest.param(
            ChatBroadcastFrame(message="hi", from_=PeerId("p1")),
            {"type": SignalType.CHAT.value, "message": "hi", "from": "p1"},
            id="the sender goes out under its wire key",
        ),
        pytest.param(
            AgentOffersFrame(shown=True, full=False, offers=[Offer("k", "Bach", pending=True)]),
            {
                "type": "agent_offers",
                "shown": True,
                "full": False,
                "offers": [{"agent_key": "k", "name": "Bach", "pending": True}],
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
