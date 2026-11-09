import asyncio
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
    HeldWrite,
    Link,
    fill_server_log,
    host_and_guest,
    resume_holding_the_first_replayed_frame,
)
from tests.support.signaling_fakes import (
    RecordingFeature,
    SocketAccountResolver,
    account_named,
    disconnect_frame,
    text_frame,
)

pytestmark = pytest.mark.integration

FOREIGN_TYPE = "quiz_start"
DECLARES_FOREIGN_TYPE = {"signal_types": {"QUIZ_START": FOREIGN_TYPE}}


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


def handler_with(room_manager, scheduler, *features):
    return SignalingHandler(room_manager, SocketAccountResolver(), scheduler, features)


@pytest_asyncio.fixture
async def link_with(room_manager, scheduler):
    """Links to handlers that carry a recording feature built with the given options, or none."""
    links = []

    def make(*feature_options):
        factories = [partial(RecordingFeature, **options) for options in feature_options]
        links.append(Link(handler_with(room_manager, scheduler, *factories)))
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
        link = link_with({**DECLARES_FOREIGN_TYPE, "handles": frozenset()})
        host = await link.join("host")

        await say_twice(host, {"type": FOREIGN_TYPE, "answer": 42})

        assert unknown_type_refusals(host) == [refusal_of_unknown(FOREIGN_TYPE)]
        assert link.handler.features[0].offered == [
            (host.peer_id, ForeignFrame(FOREIGN_TYPE, {"type": FOREIGN_TYPE, "answer": 42, "seq": 1}))
        ]

    async def test_one_the_feature_handles_is_offered_once_and_refused_never(self, link_with):
        link = link_with({**DECLARES_FOREIGN_TYPE, "handles": frozenset({FOREIGN_TYPE})})
        host = await link.join("host")

        await say_twice(host, {"type": FOREIGN_TYPE})

        assert unknown_type_refusals(host) == []
        assert [frame for _, frame in link.handler.features[0].offered] == [
            ForeignFrame(FOREIGN_TYPE, {"type": FOREIGN_TYPE, "seq": 1})
        ]


@pytest.mark.asyncio
class TestSeatJoined:
    async def test_it_fires_for_each_join_and_not_for_a_resume(self, link_with):
        link = link_with({"handles": frozenset()})
        host, guest = await host_and_guest(link)

        await guest.drop()
        await guest.resume()

        assert guest.frames(SignalType.RESUMED)
        assert link.handler.features[0].joined == [host.peer_id, guest.peer_id]

    async def test_it_does_not_fire_for_a_refused_join(self, link_with):
        link = link_with({"handles": frozenset()})
        stranger = await link.join("stranger", "no-such-room")

        assert stranger.frames(SignalType.ERROR)
        assert link.handler.features[0].joined == []


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
        link = link_with({"handles": frozenset()})
        host, guest = await host_and_guest(link)

        await ending(link, host, guest, scheduler)
        await scheduler.advance(RESUME_WINDOW_SECONDS)

        assert link.handler.features[0].left == [(guest.peer_id, room_id_of(host), "guest")]

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
        link = link_with({"handles": frozenset()})
        host, guest = await host_and_guest(link)

        await guest.drop()
        await refusal(link, host, guest, scheduler)
        heard_after_the_refusal = list(link.handler.features[0].left)
        await scheduler.advance(RESUME_WINDOW_SECONDS)

        assert heard_after_the_refusal == []
        assert link.handler.features[0].left == [(guest.peer_id, room_id_of(host), "guest")]

    async def test_a_park_a_resume_and_the_rooms_end_are_no_leave(self, link_with, scheduler):
        link = link_with({"handles": frozenset()})
        host, guest = await host_and_guest(link)

        await guest.drop()
        await scheduler.advance(RESUME_WINDOW_SECONDS - 1)
        await guest.resume()
        await scheduler.advance(RESUME_WINDOW_SECONDS)
        await host.say({"type": SignalType.END_ROOM.value})

        assert link.handler.features[0].left == []


@pytest.mark.asyncio
class TestSeatChanged:
    async def test_a_park_is_heard_with_the_seat_parked_in_its_room(self, link_with):
        link = link_with({"handles": frozenset()})
        _host, guest = await host_and_guest(link)

        await guest.drop()

        assert link.handler.features[0].changed == [(guest.peer_id, True, True)]

    async def test_a_resume_is_heard_before_the_acknowledgement_is_written(self, link_with):
        link = link_with({"handles": frozenset()})
        _host, guest = await host_and_guest(link)
        await guest.drop()
        feature = link.handler.features[0]
        feature.changed.clear()
        guest.sockets.append(await link.open(account_named("guest")))
        acknowledging = HeldWrite(guest.socket)

        guest.socket.push(text_frame(guest.resume_frame()))
        await acknowledging.reached()
        heard_while_acknowledging = list(feature.changed)
        acknowledging.release.set()

        assert heard_while_acknowledging == [(guest.peer_id, True, False)]

    async def test_a_leave_is_heard_before_the_departure_is_announced(self, link_with):
        link = link_with({"handles": frozenset()})
        host, guest = await host_and_guest(link)
        feature = link.handler.features[0]
        feature.changed.clear()
        closing = HeldWrite(host.socket)

        leaving = asyncio.create_task(guest.drop(1000))
        await closing.reached()
        heard_while_announcing = list(feature.changed)
        closing.release.set()
        await leaving

        assert heard_while_announcing == [(guest.peer_id, False, False)]

    async def test_a_refused_resume_is_not_heard_while_the_seat_stays_parked(self, link_with):
        link = link_with({"handles": frozenset()})
        _host, guest = await host_and_guest(link)
        await guest.drop()
        feature = link.handler.features[0]
        feature.changed.clear()

        await guest.resume(token="wrong")

        assert feature.changed == []


@pytest.mark.asyncio
class TestChatSaid:
    async def test_a_relayed_line_is_heard_with_its_seat_and_room_before_the_relay_sends(self, link_with):
        link = link_with({"handles": frozenset()})
        host, guest = await host_and_guest(link)
        feature = link.handler.features[0]
        relaying = HeldWrite(guest.socket)

        relayed = asyncio.create_task(host.say({"type": SignalType.CHAT.value, "message": "hello"}))
        await relaying.reached()
        heard_while_relaying = list(feature.said)
        relaying.release.set()
        await relayed

        assert heard_while_relaying == [(host.peer_id, host.room_id, "hello")]

    async def test_a_duplicate_frame_is_heard_once(self, link_with):
        link = link_with({"handles": frozenset()})
        host, _guest = await host_and_guest(link)

        await say_twice(host, {"type": SignalType.CHAT.value, "message": "hello"})

        assert link.handler.features[0].said == [(host.peer_id, host.room_id, "hello")]

    async def test_an_empty_line_is_not_heard(self, link_with):
        link = link_with({"handles": frozenset()})
        host, _guest = await host_and_guest(link)

        await host.say({"type": SignalType.CHAT.value, "message": ""})

        assert link.handler.features[0].said == []

    async def test_a_line_from_a_seat_in_no_room_is_not_heard(self, link_with):
        link = link_with({"handles": frozenset()})
        socket = await link.open(account_named("lonely"))

        socket.push(text_frame({"type": SignalType.CHAT.value, "message": "hello"}))
        await link.settle(socket)

        assert link.handler.features[0].said == []


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

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            pytest.param({"name": "same"}, {"name": "same"}, id="a feature name"),
            pytest.param({"signal_types": {"SAME": "one"}}, {"signal_types": {"SAME": "two"}}, id="a wire name"),
            pytest.param({"signal_types": {"ONE": "same"}}, {"signal_types": {"TWO": "same"}}, id="a wire value"),
        ],
    )
    def test_two_features_that_share_a_name_are_refused(self, room_manager, scheduler, first, second):
        features = [partial(RecordingFeature, **first), partial(RecordingFeature, **second)]

        with pytest.raises(FeatureClashError):
            handler_with(room_manager, scheduler, *features)


FIRST = {"name": "first", "signal_types": {"FIRST": "first_frame"}, "handles": frozenset({"first_frame"})}
SECOND = {"name": "second", "signal_types": {"SECOND": "second_frame"}, "handles": frozenset({"second_frame"})}


@pytest.mark.asyncio
class TestSeveralFeatures:
    async def test_a_frame_goes_to_the_feature_that_declared_its_type_and_to_no_other(self, link_with):
        link = link_with(FIRST, SECOND)
        host = await link.join("host")

        await host.say({"type": "second_frame"})

        first, second = link.handler.features
        assert [frame.signal_type for _, frame in first.offered] == []
        assert [frame.signal_type for _, frame in second.offered] == ["second_frame"]

    async def test_every_feature_hears_a_join_and_a_final_leave(self, link_with):
        link = link_with(FIRST, SECOND)
        host, guest = await host_and_guest(link)

        await guest.drop(1000)

        assert [feature.joined for feature in link.handler.features] == [[host.peer_id, guest.peer_id]] * 2
        assert [feature.left for feature in link.handler.features] == [[(guest.peer_id, room_id_of(host), "guest")]] * 2
