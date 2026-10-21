import asyncio
import json
from dataclasses import dataclass

import pytest
import pytest_asyncio
import structlog
import structlog.testing
from starlette.websockets import WebSocketDisconnect

from hopin.config.constants import (
    HOST_GRACE_SECONDS,
    MAX_SIGNALING_FRAME_LENGTH,
    REPLAY_LOG_LIMIT,
    RESUME_WINDOW_SECONDS,
    SEAT_CLOSED_BY_SERVER_CODE,
    ErrorReason,
    RoomClosedReason,
    SignalType,
)
from hopin.rooms.models import Account
from hopin.signaling.handler import Peer
from tests.unit.conftest import (
    ADMITTED_ACCOUNT,
    ROOM_LIMIT,
    FakeSocket,
    account_named,
    disconnect_frame,
    make_ws_mock,
    text_frame,
)

pytestmark = pytest.mark.asyncio
SETTLE_TIMEOUT_SECONDS = 5


class TestEndedRoomSeats:
    async def test_ending_the_room_closes_every_socket_and_refuses_a_resume_of_any_seat(self, link):
        host, guest = await host_and_guest(link)

        await host.say({"type": SignalType.END_ROOM.value})
        await guest.resume()
        await host.resume()

        assert host.sockets[0].closed_with == SEAT_CLOSED_BY_SERVER_CODE
        assert guest.sockets[0].closed_with == SEAT_CLOSED_BY_SERVER_CODE
        assert guest.frames(SignalType.RESUMED) == []
        assert host.frames(SignalType.RESUMED) == []
        assert guest.frames(SignalType.ERROR)[0]["reason"] == ErrorReason.RESUME_REFUSED.value

    async def test_relay_from_a_seat_of_an_ended_room_reaches_nobody(self, link):
        host, guest = await host_and_guest(link)
        await host.say({"type": SignalType.END_ROOM.value})

        await host.say({"type": SignalType.OFFER.value, "target": guest.peer_id, "sdp": "still here"})

        assert guest.frames(SignalType.OFFER) == []

    async def test_recipient_hanging_up_while_the_room_ends_leaves_no_seat_behind(self, link):
        host, guest = await host_and_guest(link)
        close_host_socket = host.socket.close

        async def close_while_guest_hangs_up(code):
            await close_host_socket(code)
            await guest.drop(1000)

        host.socket.close = close_while_guest_hangs_up

        await host.say({"type": SignalType.END_ROOM.value})
        await guest.resume()
        await host.resume()

        assert guest.frames(SignalType.RESUMED) == []
        assert host.frames(SignalType.RESUMED) == []
        assert guest.frames(SignalType.ERROR)[0]["reason"] == ErrorReason.RESUME_REFUSED.value
        assert host.frames(SignalType.ERROR)[0]["reason"] == ErrorReason.RESUME_REFUSED.value

    async def test_ending_the_room_logs_no_websocket_error_when_the_closed_sockets_report_back(self, link):
        host, guest = await host_and_guest(link)

        with structlog.testing.capture_logs() as logs:
            await host.say({"type": SignalType.END_ROOM.value})
            for browser in (host, guest):
                await browser.drop(SEAT_CLOSED_BY_SERVER_CODE)

        assert [log for log in logs if log["event"] == "websocket_error"] == []


MISSING = object()


class Browser:
    """One tab in a call: its sockets, the seat credentials it was handed and the frames it numbers."""

    def __init__(self, link, name):
        self.link = link
        self.name = name
        self.sockets = []
        self.peer_id = None
        self.token = None
        self.sent_count = 0

    @property
    def socket(self):
        return self.sockets[-1]

    @property
    def received_seq(self):
        return max((frame["seq"] for socket in self.sockets for frame in socket.sent if "seq" in frame), default=0)

    def frames(self, *signal_types):
        wanted = {signal_type.value for signal_type in signal_types}
        return [frame for socket in self.sockets for frame in socket.sent if frame["type"] in wanted]

    def take_credentials(self, frame):
        self.peer_id, self.token = frame["participant_id"], frame["resume_token"]

    async def say(self, data):
        self.sent_count += 1
        await self.send_raw({**data, "seq": self.sent_count})

    async def send_raw(self, data):
        self.socket.push(text_frame(data))
        await self.link.settle(self.socket)

    async def drop(self, code=1006):
        self.socket.push(disconnect_frame(code))
        await self.link.tasks[self.socket]

    def resume_frame(self, **overrides):
        frame = {
            "type": SignalType.RESUME.value,
            "participant_id": self.peer_id,
            "token": self.token,
            "last_seq": self.received_seq,
            "sent_floor": 0,
            **overrides,
        }
        return {key: value for key, value in frame.items() if value is not MISSING}

    async def resume(self, account=None, **overrides):
        self.sockets.append(await self.link.open(account or account_named(self.name)))
        await self.send_raw(self.resume_frame(**overrides))
        return self.socket

    async def accept_resumption(self):
        [resumed] = self.frames(SignalType.RESUMED)[-1:]
        self.token = resumed["resume_token"]


class Link:
    """Runs sockets through the handler as tasks, as the web server would."""

    def __init__(self, handler):
        self.handler = handler
        self.tasks = {}

    async def open(self, account, **socket_options):
        socket = FakeSocket([], account=account, **socket_options)
        self.tasks[socket] = asyncio.create_task(self.handler.handle_websocket(socket))
        await self.settle(socket)
        return socket

    async def settle(self, socket):
        task = self.tasks[socket]
        idle = asyncio.ensure_future(socket.idle.wait())
        done, _ = await asyncio.wait({idle, task}, timeout=SETTLE_TIMEOUT_SECONDS, return_when=asyncio.FIRST_COMPLETED)
        idle.cancel()
        if task in done:
            task.result()
        else:
            assert idle in done, "the handler neither went idle nor finished"

    async def join(self, name, room_id=None):
        browser = Browser(self, name)
        browser.sockets.append(await self.open(account_named(name)))
        await browser.send_raw({"type": SignalType.JOIN.value, "room_id": room_id})
        [first_reply] = browser.sockets[0].sent
        if "resume_token" in first_reply:
            browser.take_credentials(first_reply)
        return browser

    async def close_all(self):
        for socket, task in self.tasks.items():
            if not task.done():
                socket.push(disconnect_frame(1000))
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)


@pytest_asyncio.fixture
async def link(handler):
    link = Link(handler)
    yield link
    await link.close_all()


async def host_and_guest(link):
    host = await link.join("host")
    guest = await link.join("guest", host.socket.sent[0]["room_id"])
    return host, guest


class TestParkingAndWindow:
    async def test_resume_window_is_shorter_than_the_host_grace(self):
        assert RESUME_WINDOW_SECONDS < HOST_GRACE_SECONDS

    @pytest.mark.parametrize("code", [1005, 1006, 1011])
    @pytest.mark.parametrize(("dropper", "listener"), [("guest", "host"), ("host", "guest")])
    async def test_dropped_seat_stays_in_the_room_and_the_other_hears_neither_away_nor_left(
        self, link, room_manager, dropper, listener, code
    ):
        browsers = dict(zip(("host", "guest"), await host_and_guest(link), strict=True))
        heard_before = len(browsers[listener].socket.sent)

        await browsers[dropper].drop(code)

        assert len(browsers[listener].socket.sent) == heard_before
        assert room_manager.get_room(browsers["host"].socket.sent[0]["room_id"]).participant_count == 2

    @pytest.mark.parametrize("code", [1000, 1001])
    async def test_guest_closing_normally_leaves_at_once(self, link, code):
        host, guest = await host_and_guest(link)

        await guest.drop(code)

        assert host.frames(SignalType.PARTICIPANT_LEFT) == [
            {"type": SignalType.PARTICIPANT_LEFT.value, "participant_id": guest.peer_id, "seq": 3}
        ]

    @pytest.mark.parametrize("code", [1000, 1001])
    async def test_host_closing_normally_leaves_at_once_with_the_full_grace(self, link, code):
        host, guest = await host_and_guest(link)

        await host.drop(code)

        [away] = guest.frames(SignalType.HOST_AWAY)
        assert away["seconds_left"] == HOST_GRACE_SECONDS
        assert guest.frames(SignalType.PARTICIPANT_LEFT)

    async def test_socket_that_fails_with_an_exception_leaves_at_once(self, link):
        host, guest = await host_and_guest(link)
        guest.socket.inbox.put_nowait(None)

        with pytest.raises(TypeError):
            await link.tasks[guest.socket]

        assert host.frames(SignalType.PARTICIPANT_LEFT)

    async def test_abnormal_close_of_a_socket_in_no_room_leaves_at_once(self, link, handler):
        socket = await link.open(account_named("stranger"))

        socket.push(disconnect_frame(1006))
        await link.tasks[socket]

        assert handler._peers == {}

    @pytest.mark.parametrize("silent_seconds", [5, RESUME_WINDOW_SECONDS - 0.1])
    async def test_guest_resuming_inside_the_window_keeps_its_seat_and_the_others_hear_nothing(
        self, link, scheduler, silent_seconds
    ):
        host, guest = await host_and_guest(link)
        old_token = guest.token
        await guest.drop()
        await scheduler.advance(silent_seconds)
        heard_before = len(host.socket.sent)

        await guest.resume()

        [resumed] = guest.frames(SignalType.RESUMED)
        assert resumed["resume_token"] != old_token
        assert len(host.socket.sent) == heard_before
        await scheduler.advance(RESUME_WINDOW_SECONDS)
        assert host.frames(SignalType.PARTICIPANT_LEFT) == []

    async def test_window_running_out_removes_the_seat_for_real_and_a_late_resume_is_refused(
        self, link, scheduler, room_manager
    ):
        host, guest = await host_and_guest(link)
        await guest.drop()

        await scheduler.advance(RESUME_WINDOW_SECONDS - 1)
        assert host.frames(SignalType.PARTICIPANT_LEFT) == []
        await scheduler.advance(1)
        await guest.resume()

        [left] = host.frames(SignalType.PARTICIPANT_LEFT)
        assert left["participant_id"] == guest.peer_id
        assert guest.frames(SignalType.ERROR)[0]["reason"] == ErrorReason.RESUME_REFUSED.value
        assert room_manager.get_room(host.socket.sent[0]["room_id"]).participant_ids == [host.peer_id]

    async def test_resume_after_the_timer_fired_but_before_its_callback_ran_keeps_the_seat(
        self, link, scheduler, room_manager
    ):
        host, guest = await host_and_guest(link)
        await guest.drop()
        [window_expiry] = [timer for timer in scheduler._timers if timer.pending]
        window_expiry.pending = False

        await guest.resume()
        await window_expiry.callback()

        assert host.frames(SignalType.PARTICIPANT_LEFT) == []
        assert guest.peer_id in room_manager.get_room(host.socket.sent[0]["room_id"]).participant_ids

    async def test_stale_expiry_of_an_earlier_park_does_nothing_and_the_new_window_runs_its_full_length(
        self, link, scheduler
    ):
        host, guest = await host_and_guest(link)
        await guest.drop()
        [first_expiry] = [timer for timer in scheduler._timers if timer.pending]
        await guest.resume()
        await guest.accept_resumption()
        await guest.drop()

        await first_expiry.callback()
        await scheduler.advance(RESUME_WINDOW_SECONDS - 1)
        assert host.frames(SignalType.PARTICIPANT_LEFT) == []
        await scheduler.advance(1)

        assert len(host.frames(SignalType.PARTICIPANT_LEFT)) == 1

    async def test_parked_guest_still_counts_toward_the_room_limit(self, link):
        host = await link.join("host")
        room_id = host.socket.sent[0]["room_id"]
        guests = [await link.join(f"guest{index}", room_id) for index in range(ROOM_LIMIT - 1)]
        await guests[0].drop()

        latecomer = await link.join("latecomer", room_id)

        assert latecomer.frames(SignalType.ERROR)[0]["reason"] == ErrorReason.FULL.value


class TestParkedHost:
    async def test_host_unseen_for_the_window_is_away_with_the_rest_of_the_grace_and_the_call_ends_a_minute_on(
        self, link, scheduler, room_manager
    ):
        host, guest = await host_and_guest(link)
        await host.drop()

        await scheduler.advance(RESUME_WINDOW_SECONDS)
        [away] = guest.frames(SignalType.HOST_AWAY)
        assert away["seconds_left"] == HOST_GRACE_SECONDS - RESUME_WINDOW_SECONDS
        assert [frame["type"] for frame in guest.socket.sent[-2:]] == [
            SignalType.HOST_AWAY.value,
            SignalType.PARTICIPANT_LEFT.value,
        ]
        await scheduler.advance(HOST_GRACE_SECONDS - RESUME_WINDOW_SECONDS - 1)
        assert guest.frames(SignalType.ROOM_CLOSED) == []
        await scheduler.advance(1)

        [closed] = guest.frames(SignalType.ROOM_CLOSED)
        assert closed["reason"] == RoomClosedReason.HOST_GONE.value
        assert room_manager.has_room(host.socket.sent[0]["room_id"]) is False

    async def test_host_resuming_inside_the_window_was_never_away(self, link, scheduler, room_manager):
        host, guest = await host_and_guest(link)
        room_id = host.socket.sent[0]["room_id"]
        await host.drop()
        await scheduler.advance(20)
        heard_before = len(guest.socket.sent)

        await host.resume()
        await scheduler.advance(2 * HOST_GRACE_SECONDS)

        assert len(guest.socket.sent) == heard_before
        assert room_manager.has_room(room_id) is True

    async def test_host_account_joining_fresh_after_the_window_is_host_again_and_the_guest_hears_host_back(
        self, link, scheduler
    ):
        host, guest = await host_and_guest(link)
        await host.drop()
        await scheduler.advance(45)

        returning = await link.join("host", host.socket.sent[0]["room_id"])

        assert returning.frames(SignalType.EXISTING_PARTICIPANTS)[0]["is_host"] is True
        assert [frame["type"] for frame in guest.socket.sent[-2:]] == [
            SignalType.PARTICIPANT_JOINED.value,
            SignalType.HOST_BACK.value,
        ]

    async def test_room_ended_while_a_guest_is_parked_clears_its_seat_and_its_resume_is_refused(self, link):
        host, guest = await host_and_guest(link)
        await guest.drop()

        await host.say({"type": SignalType.END_ROOM.value})
        await guest.resume()

        assert guest.frames(SignalType.RESUMED, SignalType.ROOM_CLOSED) == []
        assert guest.frames(SignalType.ERROR)[0]["reason"] == ErrorReason.RESUME_REFUSED.value


REFUSED = ErrorReason.RESUME_REFUSED.value
RESUME_FIELDS = ("participant_id", "token", "last_seq", "sent_floor")


def only_error_reason(browser):
    return [frame["reason"] for frame in browser.frames(SignalType.ERROR)]


class TestResumeRefusals:
    @pytest.mark.parametrize(
        "resume_options",
        [
            pytest.param({"participant_id": "nobody"}, id="unknown_seat"),
            pytest.param({"token": "wrong"}, id="wrong_token"),
            pytest.param({"participant_id": 7}, id="non_string_participant_id"),
            pytest.param({"token": 7}, id="non_string_token"),
            pytest.param({"token": "tökén"}, id="non_ascii_token"),
            pytest.param({"token": "\ud800"}, id="unencodable_token"),
            pytest.param({"last_seq": None}, id="null_last_seq"),
            pytest.param({"last_seq": "1"}, id="string_last_seq"),
            pytest.param({"last_seq": 1.5}, id="float_last_seq"),
            pytest.param({"last_seq": True}, id="bool_last_seq"),
            pytest.param({"last_seq": -1}, id="negative_last_seq"),
            pytest.param({"sent_floor": None}, id="null_sent_floor"),
            pytest.param({"sent_floor": False}, id="bool_sent_floor"),
            pytest.param({"sent_floor": -1}, id="negative_sent_floor"),
            *[pytest.param({field: MISSING}, id=f"missing_{field}") for field in RESUME_FIELDS],
            pytest.param({"account": account_named("thief")}, id="right_token_from_another_account"),
        ],
    )
    async def test_unusable_resume_is_refused_with_the_one_bare_reason_and_the_seat_stays_parked(
        self, link, resume_options
    ):
        _host, guest = await host_and_guest(link)
        await guest.drop()

        refusal_socket = await guest.resume(**resume_options)

        assert refusal_socket.sent == [{"type": SignalType.ERROR.value, "reason": REFUSED, "message": "Resume refused"}]
        await guest.resume()
        assert guest.frames(SignalType.RESUMED)

    async def test_right_token_from_the_same_account_id_under_another_display_name_resumes(self, link):
        _host, guest = await host_and_guest(link)
        await guest.drop()

        await guest.resume(account=Account(id="guest", name="Guest, renamed"))

        assert guest.frames(SignalType.RESUMED)

    async def test_token_rotated_out_by_a_resume_is_refused(self, link):
        _host, guest = await host_and_guest(link)
        await guest.drop()
        await guest.resume()
        stale_token = guest.token
        await guest.accept_resumption()
        await guest.drop()

        await guest.resume(token=stale_token)

        assert only_error_reason(guest) == [REFUSED]

    async def test_resume_as_a_later_frame_from_another_seat_is_refused_and_the_parked_seat_still_resumes(self, link):
        host, guest = await host_and_guest(link)
        await guest.drop()
        other_seat = await link.join("guest", host.socket.sent[0]["room_id"])
        heard_before = len(host.socket.sent)

        await other_seat.say(guest.resume_frame())

        assert only_error_reason(other_seat) == [REFUSED]
        assert len(host.socket.sent) == heard_before
        await guest.resume()
        assert guest.frames(SignalType.RESUMED)


@dataclass
class HeldReplay:
    reached: asyncio.Event
    release: asyncio.Event


async def resume_holding_the_first_replayed_frame(link, browser):
    held = HeldReplay(reached=asyncio.Event(), release=asyncio.Event())
    socket = await link.open(account_named(browser.name))
    write = socket.send_text

    async def hold_replayed_frames(text):
        if socket.sent:
            held.reached.set()
            await held.release.wait()
        await write(text)

    socket.send_text = hold_replayed_frames
    browser.sockets.append(socket)
    socket.push(text_frame(browser.resume_frame()))
    return held


async def fill_server_log(host, frames):
    for index in range(frames):
        await host.say({"type": SignalType.CHAT.value, "message": f"message {index}"})


class TestResumeLogs:
    @pytest.mark.parametrize(
        ("frames_beyond_the_log", "resume_options"),
        [
            pytest.param(REPLAY_LOG_LIMIT + 5, {"last_seq": 0}, id="server_log_no_longer_reaches_the_last_frame"),
            pytest.param(0, {"sent_floor": 1}, id="browser_log_starts_beyond_what_the_server_received"),
        ],
    )
    async def test_log_that_cannot_bridge_the_gap_refuses_and_releases_the_seat(
        self, link, frames_beyond_the_log, resume_options
    ):
        host, guest = await host_and_guest(link)
        await guest.drop()
        await fill_server_log(host, frames_beyond_the_log)

        await guest.resume(**resume_options)

        assert only_error_reason(guest) == [REFUSED]
        [left] = host.frames(SignalType.PARTICIPANT_LEFT)
        assert left["participant_id"] == guest.peer_id

    async def test_releasing_an_unresumable_seat_closes_the_old_socket_that_is_still_attached(self, link):
        host, guest = await host_and_guest(link)
        old_socket = guest.socket

        await guest.resume(sent_floor=1)

        assert old_socket.closed_with == SEAT_CLOSED_BY_SERVER_CODE
        assert host.frames(SignalType.PARTICIPANT_LEFT)
        old_socket.push(disconnect_frame(1006))
        await link.tasks[old_socket]
        assert len(host.frames(SignalType.PARTICIPANT_LEFT)) == 1

    @pytest.mark.parametrize("claimed_extra", [0, 1])
    async def test_gaps_left_by_superseded_frames_do_not_stop_a_resume_and_only_the_newest_description_comes_back(
        self, link, claimed_extra
    ):
        host, guest = await host_and_guest(link)
        await guest.drop()
        seen = guest.received_seq
        await host.say({"type": SignalType.OFFER.value, "target": guest.peer_id, "sdp": "first"})
        await host.say({"type": SignalType.OFFER.value, "target": guest.peer_id, "sdp": "second"})

        await guest.resume(last_seq=seen + claimed_extra)

        assert [frame["sdp"] for frame in guest.frames(SignalType.OFFER)] == ["second"]

    async def test_candidates_before_a_newer_description_are_dropped_and_the_ones_after_it_replayed(self, link):
        host, guest = await host_and_guest(link)
        await guest.drop()
        target = guest.peer_id
        await host.say({"type": SignalType.OFFER.value, "target": target, "sdp": "old"})
        await host.say({"type": SignalType.ICE_CANDIDATE.value, "target": target, "candidate": "old-candidate"})
        await host.say({"type": SignalType.OFFER.value, "target": target, "sdp": "new"})
        await host.say({"type": SignalType.ICE_CANDIDATE.value, "target": target, "candidate": "new-candidate"})

        await guest.resume()

        replayed = guest.frames(SignalType.OFFER, SignalType.ICE_CANDIDATE)
        assert [frame.get("sdp") or frame["candidate"] for frame in replayed] == ["new", "new-candidate"]

    async def test_newcomer_offer_and_chat_missed_while_parked_are_replayed_in_order_after_resumed(self, link):
        host, guest = await host_and_guest(link)
        await guest.drop()
        newcomer = await link.join("newcomer", host.socket.sent[0]["room_id"])
        await newcomer.say({"type": SignalType.OFFER.value, "target": guest.peer_id, "sdp": "hello"})
        await host.say({"type": SignalType.CHAT.value, "message": "hi"})

        await guest.resume()

        assert [frame["type"] for frame in guest.socket.sent] == [
            SignalType.RESUMED.value,
            SignalType.PARTICIPANT_JOINED.value,
            SignalType.OFFER.value,
            SignalType.CHAT.value,
        ]
        assert [frame["seq"] for frame in guest.socket.sent[1:]] == sorted(
            frame["seq"] for frame in guest.socket.sent[1:]
        )
        assert host.socket.sent[-1]["type"] != SignalType.CHAT.value

    async def test_parked_seat_is_written_nothing_while_it_is_away(self, link):
        host, guest = await host_and_guest(link)
        dropped_socket = guest.socket
        await guest.drop()
        written_before = len(dropped_socket.sent)

        await host.say({"type": SignalType.CHAT.value, "message": "hi"})

        assert len(dropped_socket.sent) == written_before

    async def test_frame_sent_while_a_replayed_frame_is_being_written_arrives_after_the_replayed_ones(self, link):
        host, guest = await host_and_guest(link)
        await guest.drop()
        await host.say({"type": SignalType.CHAT.value, "message": "missed 1"})
        await host.say({"type": SignalType.CHAT.value, "message": "missed 2"})
        writing_replay = await resume_holding_the_first_replayed_frame(link, guest)

        await writing_replay.reached.wait()
        await host.say({"type": SignalType.CHAT.value, "message": "live"})
        writing_replay.release.set()
        await link.settle(guest.socket)

        assert [frame["message"] for frame in guest.frames(SignalType.CHAT)] == ["missed 1", "missed 2", "live"]
        seqs = [frame["seq"] for frame in guest.socket.sent[1:]]
        assert seqs == sorted(set(seqs))

    async def test_log_overrun_while_a_replayed_frame_is_being_written_releases_the_seat_instead_of_skipping_frames(
        self, link
    ):
        host, guest = await host_and_guest(link)
        await guest.drop()
        await host.say({"type": SignalType.CHAT.value, "message": "missed"})
        writing_replay = await resume_holding_the_first_replayed_frame(link, guest)

        await writing_replay.reached.wait()
        await fill_server_log(host, REPLAY_LOG_LIMIT + 5)
        writing_replay.release.set()
        await link.settle(guest.socket)

        assert [frame["message"] for frame in guest.frames(SignalType.CHAT)] == ["missed"]
        assert guest.socket.closed_with == SEAT_CLOSED_BY_SERVER_CODE
        [left] = host.frames(SignalType.PARTICIPANT_LEFT)
        assert left["participant_id"] == guest.peer_id

    async def test_superseded_replay_overrunning_the_log_leaves_the_seat_of_the_newer_resume_alone(self, link):
        host, guest = await host_and_guest(link)
        await guest.drop()
        await host.say({"type": SignalType.CHAT.value, "message": "missed"})
        writing_replay = await resume_holding_the_first_replayed_frame(link, guest)
        await writing_replay.reached.wait()
        superseded_socket = guest.socket
        await guest.accept_resumption()
        newer_socket = await guest.resume()
        await fill_server_log(host, REPLAY_LOG_LIMIT + 5)

        writing_replay.release.set()
        await link.settle(superseded_socket)

        assert newer_socket.closed_with is None
        assert host.frames(SignalType.PARTICIPANT_LEFT) == []
        await host.say({"type": SignalType.CHAT.value, "message": "live"})
        assert newer_socket.sent[-1]["message"] == "live"

    async def test_replay_stops_when_the_socket_breaks_and_the_next_resume_gets_the_rest(self, link):
        host, guest = await host_and_guest(link)
        await guest.drop()
        await host.say({"type": SignalType.CHAT.value, "message": "missed"})
        failing = await link.open(account_named("guest"))
        deliver_resumed = failing.send_text

        async def break_after_the_first_frame(text):
            if failing.sent:
                raise WebSocketDisconnect(code=1006)
            await deliver_resumed(text)

        failing.send_text = break_after_the_first_frame
        guest.sockets.append(failing)
        await guest.send_raw(guest.resume_frame())
        await guest.accept_resumption()

        await guest.drop()
        await guest.resume()

        assert [frame["message"] for frame in guest.frames(SignalType.CHAT)] == ["missed"]


class TestResumeSupersedesTheOldSocket:
    async def test_resume_closes_the_old_socket_and_nothing_it_says_or_does_afterwards_counts(self, link):
        host, guest = await host_and_guest(link)
        old_socket = guest.socket
        await guest.resume()
        heard_before = len(host.socket.sent)

        old_socket.push(text_frame({"type": SignalType.CHAT.value, "message": "ghost", "seq": 1}))
        old_socket.push(disconnect_frame(1006))
        await link.tasks[old_socket]

        assert old_socket.closed_with == SEAT_CLOSED_BY_SERVER_CODE
        assert len(host.socket.sent) == heard_before
        await guest.say({"type": SignalType.CHAT.value, "message": "from the new socket"})
        assert host.socket.sent[-1]["message"] == "from the new socket"

    async def test_resume_while_the_old_socket_is_already_gone_closes_nothing(self, link):
        _host, guest = await host_and_guest(link)
        old_socket = guest.socket
        await guest.drop()

        await guest.resume()

        assert old_socket.closed_with is None

    async def test_old_socket_that_fails_to_close_does_not_stop_the_resume(self, link):
        _host, guest = await host_and_guest(link)

        async def broken_close(code):
            raise WebSocketDisconnect(code=code)

        guest.socket.close = broken_close
        await guest.resume()

        assert guest.frames(SignalType.RESUMED)


class TestSequenceNumbers:
    async def test_frames_to_a_seat_carry_rising_sequence_numbers_including_its_credentials(self, link):
        host, guest = await host_and_guest(link)
        await host.say({"type": SignalType.CHAT.value, "message": "hi"})

        assert [frame["seq"] for frame in guest.socket.sent] == [1, 2]
        assert host.socket.sent[0]["seq"] == 1

    async def test_frame_resent_at_or_below_the_received_number_is_not_relayed_twice(self, link):
        host, guest = await host_and_guest(link)
        chat = {"type": SignalType.CHAT.value, "message": "once"}
        await guest.say(chat)

        await guest.send_raw({**chat, "seq": 1})

        assert [frame["message"] for frame in host.frames(SignalType.CHAT)] == ["once"]

    async def test_frame_swallowed_before_the_cut_is_relayed_once_when_the_browser_resends_it_after_the_resume(
        self, link
    ):
        host, guest = await host_and_guest(link)
        await guest.drop()
        guest.sent_count += 1

        await guest.resume()
        [resumed] = guest.frames(SignalType.RESUMED)
        assert resumed["last_seq"] == 0
        await guest.send_raw(
            {"type": SignalType.ANSWER.value, "target": host.peer_id, "sdp": "swallowed", "seq": guest.sent_count}
        )

        assert [frame["sdp"] for frame in host.frames(SignalType.ANSWER)] == ["swallowed"]

    @pytest.mark.parametrize("seq", [pytest.param(None, id="missing"), "1", 1.5, True, -1])
    async def test_later_frame_without_a_usable_sequence_number_is_answered_invalid_message(self, link, seq):
        host, guest = await host_and_guest(link)
        frame = {"type": SignalType.CHAT.value, "message": "hi"}

        await guest.send_raw(frame if seq is None else {**frame, "seq": seq})

        assert only_error_reason(guest) == [ErrorReason.INVALID_MESSAGE.value]
        assert host.frames(SignalType.CHAT) == []


def chat_frame_of_length(length, seq):
    unpadded = json.dumps({"type": SignalType.CHAT.value, "message": "", "seq": seq})
    return {"type": SignalType.CHAT.value, "message": "x" * (length - len(unpadded)), "seq": seq}


class TestFrameLength:
    async def test_frame_of_exactly_the_bound_is_relayed(self, link):
        host, guest = await host_and_guest(link)

        await guest.send_raw(chat_frame_of_length(MAX_SIGNALING_FRAME_LENGTH, seq=1))

        assert len(host.frames(SignalType.CHAT)) == 1

    async def test_frame_longer_than_the_bound_is_refused_and_neither_relayed_nor_replayed(self, link):
        host, guest = await host_and_guest(link)

        await guest.send_raw(chat_frame_of_length(MAX_SIGNALING_FRAME_LENGTH + 1, seq=1))

        assert only_error_reason(guest) == [ErrorReason.INVALID_MESSAGE.value]
        assert host.frames(SignalType.CHAT) == []
        await host.drop()
        await host.resume()
        assert host.frames(SignalType.CHAT) == []


class TestTokenStaysSecret:
    async def test_no_log_value_of_a_park_a_resume_or_a_refusal_holds_a_token(self, link):
        host, guest = await host_and_guest(link)
        first_token = guest.token

        with structlog.testing.capture_logs() as logs:
            await guest.drop()
            await guest.resume()
            await guest.accept_resumption()
            await guest.drop()
            await guest.resume(token=first_token)

        assert {"seat_parked", "seat_resumed", "resume_refused"} <= {log["event"] for log in logs}
        for secret in (first_token, guest.token, host.token):
            assert secret not in json.dumps(logs, default=str)

    async def test_no_frame_to_another_member_carries_a_token_or_another_members_peer_credentials(self, link):
        host, guest = await host_and_guest(link)
        await guest.drop()
        await guest.resume()
        await guest.accept_resumption()
        await guest.say({"type": SignalType.CHAT.value, "message": "hi"})

        heard_by_host = json.dumps(host.socket.sent[1:])
        assert guest.token not in heard_by_host
        assert "resume_token" not in heard_by_host
        assert host.token not in json.dumps(guest.sockets[-1].sent)

    async def test_seat_reprs_hide_the_token(self):
        seat = Peer(account=ADMITTED_ACCOUNT, websocket=make_ws_mock())

        assert seat.resume_token not in repr(seat)
