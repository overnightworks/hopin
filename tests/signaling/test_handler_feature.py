from functools import partial

import pytest
import pytest_asyncio

from hopin.config.constants import (
    REPLAY_LOG_LIMIT,
    RESUME_WINDOW_SECONDS,
    SESSION_RECHECK_SECONDS,
    ErrorReason,
    SignalType,
)
from hopin.signaling.features import FeatureClashError
from hopin.signaling.frames import ForeignFrame
from hopin.signaling.handler import SignalingHandler
from tests.signaling.support.browsers import (
    Link,
    fill_server_log,
    host_and_guest,
    resume_holding_the_first_replayed_frame,
)
from tests.support.signaling_fakes import RecordingFeature, SocketAccountResolver, account_named, disconnect_frame

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


def room_id_of(browser):
    return browser.socket.sent[0]["room_id"]


async def leaves(link, _host, guest, _scheduler):
    await guest.drop(code=1000)


async def session_ends(_link, _host, guest, scheduler):
    guest.socket.account = None
    await scheduler.advance(SESSION_RECHECK_SECONDS)
    await guest.drop()


async def grace_runs_out(_link, _host, guest, _scheduler):
    await guest.drop()


async def refused_for_a_log_gap(_link, host, guest, _scheduler):
    await guest.drop()
    await fill_server_log(host, REPLAY_LOG_LIMIT + 5)
    await guest.resume(last_seq=0)


async def refused_for_a_browser_log_beyond_the_server(_link, _host, guest, _scheduler):
    await guest.drop()
    await guest.resume(sent_floor=1)


async def refused_for_a_browser_log_beyond_the_server_while_attached(link, _host, guest, _scheduler):
    old_socket = guest.socket
    await guest.resume(sent_floor=1)
    old_socket.push(disconnect_frame(1006))
    await link.tasks[old_socket]


async def overrun_by_the_log_while_replaying(link, host, guest, _scheduler):
    await guest.drop()
    await host.say({"type": SignalType.CHAT.value, "message": "missed"})
    replay = await resume_holding_the_first_replayed_frame(link, guest)
    await replay.reached.wait()
    await fill_server_log(host, REPLAY_LOG_LIMIT + 5)
    replay.release.set()
    await link.settle(guest.socket)
    guest.socket.push(disconnect_frame(1006))
    await link.tasks[guest.socket]


async def resume_with_a_wrong_token(_link, _host, guest, _scheduler):
    wrong_token = {"token": "wrong"}
    await guest.resume(**wrong_token)


async def resume_from_another_account(_link, _host, guest, _scheduler):
    await guest.resume(account=account_named("thief"))


async def resume_as_a_later_frame_of_another_seat(link, host, guest, _scheduler):
    other_seat = await link.join("guest", room_id_of(host))
    await other_seat.say(guest.resume_frame())


async def resume_of_an_unknown_seat(_link, _host, guest, _scheduler):
    await guest.resume(participant_id="nobody")


@pytest.mark.asyncio
class TestSeatLeft:
    @pytest.mark.parametrize(
        "ending",
        [
            pytest.param(leaves, id="a leave"),
            pytest.param(session_ends, id="an ended session"),
            pytest.param(grace_runs_out, id="a grace that ran out"),
            pytest.param(refused_for_a_log_gap, id="a resume the server log cannot bridge"),
            pytest.param(refused_for_a_browser_log_beyond_the_server, id="a resume beyond the browser log"),
            pytest.param(refused_for_a_browser_log_beyond_the_server_while_attached, id="the same on an attached seat"),
            pytest.param(overrun_by_the_log_while_replaying, id="a replay that overruns the log"),
        ],
    )
    async def test_the_feature_hears_a_final_leave_once_with_the_room_and_the_name(self, link_with, scheduler, ending):
        link = link_with(handles=frozenset())
        host, guest = await host_and_guest(link)

        await ending(link, host, guest, scheduler)
        await scheduler.advance(RESUME_WINDOW_SECONDS)

        assert link.handler.feature.left == [(guest.peer_id, room_id_of(host), "guest")]

    @pytest.mark.parametrize(
        "refusal",
        [
            pytest.param(resume_with_a_wrong_token, id="a wrong token"),
            pytest.param(resume_from_another_account, id="another account"),
            pytest.param(resume_as_a_later_frame_of_another_seat, id="a later frame of another seat"),
            pytest.param(resume_of_an_unknown_seat, id="an unknown seat"),
        ],
    )
    async def test_a_refused_resume_that_keeps_the_seat_is_no_leave_until_its_grace_runs_out(
        self, link_with, scheduler, refusal
    ):
        link = link_with(handles=frozenset())
        host, guest = await host_and_guest(link)

        await guest.drop()
        await refusal(link, host, guest, scheduler)
        heard_after_the_refusal = list(link.handler.feature.left)
        await scheduler.advance(RESUME_WINDOW_SECONDS)

        assert heard_after_the_refusal == []
        assert link.handler.feature.left == [(guest.peer_id, room_id_of(host), "guest")]

    async def test_a_park_a_resume_and_the_rooms_end_are_no_leave(self, link_with, scheduler):
        link = link_with(handles=frozenset())
        host, guest = await host_and_guest(link)

        await guest.drop()
        await scheduler.advance(RESUME_WINDOW_SECONDS - 1)
        await guest.resume()
        await scheduler.advance(RESUME_WINDOW_SECONDS)
        await host.say({"type": SignalType.END_ROOM.value})

        assert link.handler.feature.left == []


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
