from functools import partial

import pytest
import pytest_asyncio

from hopin.config.constants import ErrorReason, SignalType
from hopin.signaling.features import FeatureClashError
from hopin.signaling.frames import ForeignFrame
from hopin.signaling.handler import SignalingHandler
from tests.signaling.support.browsers import Link, host_and_guest
from tests.support.signaling_fakes import RecordingFeature, SocketAccountResolver

pytestmark = pytest.mark.integration

FOREIGN_TYPE = "quiz_start"


def without_seq(frame):
    return {key: value for key, value in frame.items() if key != "seq"}


def unknown_type_refusals(browser):
    return [without_seq(frame) for frame in browser.frames(SignalType.ERROR)]


def refusal_of_unknown(signal_type):
    return {
        "type": SignalType.ERROR.value,
        "reason": ErrorReason.INVALID_MESSAGE.value,
        "message": f"Unknown signal type: {signal_type}",
    }


def handler_with(room_manager, scheduler, feature):
    return SignalingHandler(room_manager, SocketAccountResolver(), scheduler, feature)


@pytest_asyncio.fixture
async def link_with(room_manager, scheduler):
    """Links to handlers that carry a recording feature built with the given options, or none."""
    links = []

    def make(**feature_options):
        feature = partial(RecordingFeature, **feature_options) if feature_options else None
        links.append(Link(handler_with(room_manager, scheduler, feature)))
        return links[-1]

    yield make
    for link in links:
        await link.close_all()


async def say_twice(browser, data):
    """Send the frame, then the same frame again under the same sequence number, as a resent duplicate."""
    await browser.say(data)
    await browser.send_raw({**data, "seq": browser.sent_count})


@pytest.mark.asyncio
class TestUnknownSignalType:
    async def test_without_a_feature_it_is_refused_once_after_its_sequence_number_counts(self, link_with):
        link = link_with()
        host = await link.join("host")

        await say_twice(host, {"type": FOREIGN_TYPE})

        assert unknown_type_refusals(host) == [refusal_of_unknown(FOREIGN_TYPE)]

    async def test_one_the_feature_declines_is_refused_once_after_its_sequence_number_counts(self, link_with):
        link = link_with(handles=frozenset())
        host = await link.join("host")

        await say_twice(host, {"type": FOREIGN_TYPE, "answer": 42})

        assert unknown_type_refusals(host) == [refusal_of_unknown(FOREIGN_TYPE)]
        assert link.handler.feature.offered == [
            (host.peer_id, ForeignFrame(FOREIGN_TYPE, {"type": FOREIGN_TYPE, "answer": 42, "seq": 1}))
        ]

    async def test_one_the_feature_handles_is_offered_once_and_refused_never(self, link_with):
        link = link_with(handles=frozenset({FOREIGN_TYPE}))
        host = await link.join("host")

        await say_twice(host, {"type": FOREIGN_TYPE})

        assert unknown_type_refusals(host) == []
        assert [frame for _, frame in link.handler.feature.offered] == [
            ForeignFrame(FOREIGN_TYPE, {"type": FOREIGN_TYPE, "seq": 1})
        ]


@pytest.mark.asyncio
class TestSeatJoined:
    async def test_it_fires_for_each_join_and_not_for_a_resume(self, link_with):
        link = link_with(handles=frozenset())
        host, guest = await host_and_guest(link)

        await guest.drop()
        await guest.resume()

        assert guest.frames(SignalType.RESUMED)
        assert link.handler.feature.joined == [host.peer_id, guest.peer_id]

    async def test_it_does_not_fire_for_a_refused_join(self, link_with):
        link = link_with(handles=frozenset())
        stranger = await link.join("stranger", "no-such-room")

        assert stranger.frames(SignalType.ERROR)
        assert link.handler.feature.joined == []


class TestClashAtStart:
    @pytest.mark.parametrize(
        "signal_types",
        [
            pytest.param({"JOIN": FOREIGN_TYPE}, id="a core name"),
            pytest.param({"QUIZ_START": SignalType.CHAT.value}, id="a core value"),
        ],
    )
    def test_a_feature_signal_type_that_reuses_a_core_one_is_refused(self, room_manager, scheduler, signal_types):
        clashing = partial(RecordingFeature, signal_types=signal_types)

        with pytest.raises(FeatureClashError):
            handler_with(room_manager, scheduler, clashing)
