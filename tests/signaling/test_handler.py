import asyncio
import secrets

import pytest
import pytest_asyncio
import structlog
import structlog.testing
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from hopin.config.constants import (
    HOST_GRACE_SECONDS,
    RESUME_WINDOW_SECONDS,
    ErrorReason,
    RoomClosedReason,
    SignalType,
)
from hopin.rooms.models import Account
from hopin.signaling.handler import SignalingHandler
from tests.signaling.support.browsers import Browser, Link
from tests.support.signaling_fakes import (
    ADMITTED_ACCOUNT,
    ROOM_LIMIT,
    FakeGameMaster,
    FakeSocket,
    ManualScheduler,
    SocketAccountResolver,
    account_named,
    disconnect_frame,
    text_frame,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class ResolverError(Exception):
    pass


class RaisingIdentityResolver:
    async def resolve(self, _websocket):
        raise ResolverError


async def raise_disconnect(_text):
    raise WebSocketDisconnect(code=1006)


@pytest_asyncio.fixture
async def link(handler):
    link = Link(handler)
    yield link
    await link.close_all()


async def connect(link, name):
    """A browser whose socket is open but has not joined a room."""
    browser = Browser(link, name)
    browser.sockets.append(await link.open(account_named(name)))
    return browser


def clear_sent(*browsers):
    """Forget what the browsers were sent so far, so a test judges only what follows its arrangement."""
    for browser in browsers:
        for socket in browser.sockets:
            socket.sent.clear()


async def seat_in_room(link, room_manager, host, *guests):
    """The host and guests joined into one room by real joins, with the join chatter forgotten."""
    browsers = {host: await link.join(host)}
    room_id = browsers[host].sockets[0].sent[0]["room_id"]
    for guest in guests:
        browsers[guest] = await link.join(guest, room_id)
    clear_sent(*browsers.values())
    return room_manager.get_room(room_id), browsers


async def deliver(browser, message):
    """Hand the handler the browser's next frame: a string as typed, a dict numbered as a browser would."""
    if isinstance(message, str):
        browser.socket.push({"type": "websocket.receive", "text": message})
        await browser.link.settle(browser.socket)
    else:
        await browser.say(message)


def sent_frames(browser):
    """The frames the browser was sent, without the sequence numbers a test about content does not care for."""
    return [
        {key: value for key, value in frame.items() if key != "seq"}
        for socket in browser.sockets
        for frame in socket.sent
    ]


def sent_types(browser):
    return [frame["type"] for frame in sent_frames(browser)]


async def join_unknown_room(_link, _room_manager, _sender):
    return {"type": SignalType.JOIN.value, "room_id": "nonexistent"}


async def join_locked_room(_link, room_manager, _sender):
    room = room_manager.create_room("host1", account_named("host1"))
    room_manager.set_room_locked(room.room_id, account_named("host1"), locked=True)
    return {"type": SignalType.JOIN.value, "room_id": room.room_id}


async def join_full_room(_link, room_manager, _sender):
    room = room_manager.create_room("host1", account_named("host1"))
    for index in range(ROOM_LIMIT - 1):
        room_manager.add_participant(room.room_id, f"member{index}", account_named(f"member{index}"))
    return {"type": SignalType.JOIN.value, "room_id": room.room_id}


def stranger_sends(request):
    async def arrange(_link, _room_manager, _sender):
        return request

    return arrange


async def join_host_room(link, sender):
    host = await link.join("host1")
    await sender.send_raw(join_message(host.sockets[0].sent[0]["room_id"]))
    return host


async def lock_as_participant(link, _room_manager, sender):
    await join_host_room(link, sender)
    return {"type": SignalType.LOCK_ROOM.value}


async def end_room_as_participant(link, _room_manager, sender):
    await join_host_room(link, sender)
    return {"type": SignalType.END_ROOM.value}


async def lock_while_host_is_away(link, _room_manager, sender):
    host = await join_host_room(link, sender)
    await host.drop(1000)
    return {"type": SignalType.LOCK_ROOM.value}


class TestHandleMessageRefusals:
    @pytest.mark.parametrize(
        ("reason", "arrange", "detail"),
        [
            pytest.param(ErrorReason.NOT_FOUND, join_unknown_room, None, id="join_unknown_room"),
            pytest.param(ErrorReason.LOCKED, join_locked_room, None, id="join_locked_room"),
            pytest.param(ErrorReason.FULL, join_full_room, None, id="join_full_room"),
            pytest.param(ErrorReason.NOT_HOST, lock_as_participant, None, id="lock_as_participant"),
            pytest.param(ErrorReason.NOT_HOST, end_room_as_participant, None, id="end_room_as_participant"),
            pytest.param(ErrorReason.NOT_HOST, lock_while_host_is_away, None, id="lock_while_host_is_away"),
            pytest.param(ErrorReason.INVALID_MESSAGE, stranger_sends("not json"), "Invalid JSON", id="not_json"),
            pytest.param(
                ErrorReason.INVALID_MESSAGE, stranger_sends("[]"), "Message must be a JSON object", id="json_array"
            ),
            pytest.param(
                ErrorReason.INVALID_MESSAGE, stranger_sends({"foo": "bar"}), "Missing signal type", id="no_type"
            ),
            pytest.param(
                ErrorReason.INVALID_MESSAGE,
                stranger_sends({"type": "unknown"}),
                "Unknown signal type: unknown",
                id="unknown_type",
            ),
            pytest.param(
                ErrorReason.INVALID_MESSAGE, stranger_sends({"type": []}), "Missing signal type", id="non_string_type"
            ),
            pytest.param(
                ErrorReason.INVALID_MESSAGE,
                stranger_sends({"type": SignalType.OFFER.value, "target": []}),
                "target must be a string",
                id="non_string_target",
            ),
            pytest.param(
                ErrorReason.INVALID_MESSAGE,
                stranger_sends({"type": SignalType.JOIN.value, "room_id": {}}),
                "room_id must be a string",
                id="non_string_room_id",
            ),
        ],
    )
    async def test_handle_message_refused_request_sends_one_error_frame_with_reason_and_message(
        self, link, room_manager, reason, arrange, detail
    ):
        sender = await connect(link, "sender")
        raw_data = await arrange(link, room_manager, sender)
        clear_sent(sender)

        await deliver(sender, raw_data)

        [response] = sender.socket.sent
        assert response["type"] == SignalType.ERROR.value
        assert response["reason"] == reason.value
        assert response["message"]
        # A room refusal's wording names the room id the arrangement generated, so its row leaves the detail open
        if detail is not None:
            assert response["message"] == detail

    async def test_handle_message_full_room_refusal_carries_the_capacity(self, link, room_manager):
        sender = await connect(link, "sender")
        raw_data = await join_full_room(link, room_manager, sender)

        await deliver(sender, raw_data)

        response = sender.socket.sent[-1]
        assert response["capacity"] == ROOM_LIMIT


class TestJoinCreateRoom:
    async def test_creating_a_room_answers_room_created_and_seats_the_creator(self, link, room_manager):
        host = await connect(link, "host1")

        await deliver(host, join_message())
        await deliver(host, {"type": SignalType.LOCK_ROOM.value})

        assert sent_types(host) == [SignalType.ROOM_CREATED.value, SignalType.ROOM_LOCKED.value]
        assert room_manager.get_room(sent_frames(host)[0]["room_id"]).locked


class TestJoinExistingRoom:
    async def test_second_join_from_a_seat_in_a_room_is_refused_and_the_first_room_is_unchanged(
        self, link, room_manager
    ):
        room, browsers = await seat_in_room(link, room_manager, "host1", "p1")
        other_room = room_manager.create_room("host2", account_named("host2"))

        await deliver(browsers["p1"], join_message(other_room.room_id))
        await deliver(browsers["p1"], join_message())
        await deliver(browsers["p1"], {"type": SignalType.CHAT.value, "message": "still here"})

        assert [frame["reason"] for frame in frames_of_type(browsers["p1"], SignalType.ERROR)] == [
            ErrorReason.INVALID_MESSAGE.value
        ] * 2
        assert frames_of_type(browsers["host1"], SignalType.CHAT) == [
            {"type": SignalType.CHAT.value, "message": "still here", "from": browsers["p1"].peer_id}
        ]
        assert room.participant_ids == [browsers["host1"].peer_id, browsers["p1"].peer_id]
        assert other_room.participant_ids == ["host2"]

    async def test_refused_second_join_is_not_logged_as_a_join(self, link, room_manager):
        _room, browsers = await seat_in_room(link, room_manager, "host1", "p1")

        with structlog.testing.capture_logs() as logs:
            await deliver(browsers["p1"], join_message())

        assert [log["event"] for log in logs if log["event"] in ("room_created", "participant_joined")] == []

    async def test_joiner_gets_the_existing_participants_and_they_hear_of_the_joiner(self, link, room_manager):
        room, browsers = await seat_in_room(link, room_manager, "host1")

        joiner = await link.join("p1", room.room_id)

        [existing] = frames_of_type(joiner, SignalType.EXISTING_PARTICIPANTS)
        assert browsers["host1"].peer_id in existing["participants"]
        assert sent_frames(browsers["host1"]) == [
            {"type": SignalType.PARTICIPANT_JOINED.value, "participant_id": joiner.peer_id, "name": "p1"}
        ]

    async def test_joiner_is_told_the_names_of_the_others_by_their_participant_ids(self, handler):
        host_socket = FakeSocket([text_frame(join_message())], account=Account(id="host-account", name="felix"))
        host_connection = asyncio.create_task(handler.handle_websocket(host_socket))
        await host_socket.idle.wait()
        joiner_socket = FakeSocket(
            [text_frame(join_message(host_socket.sent[0]["room_id"])), disconnect_frame(1000)],
            account=account_named("ben"),
        )

        await handler.handle_websocket(joiner_socket)
        host_socket.push(disconnect_frame(1000))
        await host_connection

        [existing] = [frame for frame in joiner_socket.sent if frame["type"] == SignalType.EXISTING_PARTICIPANTS.value]
        assert existing["names"] == {host_socket.sent[0]["participant_id"]: "felix"}


class TestRelay:
    @pytest.mark.parametrize(
        ("signal_type", "payload"),
        [
            (SignalType.OFFER, {"sdp": "test_sdp"}),
            (SignalType.ANSWER, {"sdp": "answer_sdp"}),
            (SignalType.ICE_CANDIDATE, {"candidate": "test_candidate"}),
        ],
    )
    async def test_signal_is_relayed_to_the_target_with_its_sender(self, link, room_manager, signal_type, payload):
        _room, browsers = await seat_in_room(link, room_manager, "p1", "p2")

        await deliver(browsers["p1"], {"type": signal_type.value, "target": browsers["p2"].peer_id, **payload})

        [relayed] = sent_frames(browsers["p2"])
        assert {"type": signal_type.value, "from": browsers["p1"].peer_id, **payload}.items() <= relayed.items()

    @pytest.mark.parametrize("signal_type", [SignalType.OFFER, SignalType.ANSWER, SignalType.ICE_CANDIDATE])
    @pytest.mark.parametrize(
        ("sender", "target"),
        [
            pytest.param("p1", None, id="without_target"),
            pytest.param("p1", "outsider", id="to_a_peer_in_another_room"),
            pytest.param("p1", "never-seated", id="to_a_peer_that_holds_no_seat_in_the_room"),
            pytest.param("loner", "p1", id="from_a_sender_in_no_room"),
        ],
    )
    async def test_relay_that_has_no_recipient_in_the_senders_room_reaches_nobody(
        self, link, room_manager, signal_type, sender, target
    ):
        _room, browsers = await seat_in_room(link, room_manager, "p1", "p2")
        _other_room, other_browsers = await seat_in_room(link, room_manager, "outsider")
        browsers |= other_browsers | {"loner": await connect(link, "loner")}
        route = {} if target is None else {"target": browsers[target].peer_id if target in browsers else target}

        await deliver(browsers[sender], {"type": signal_type.value, **route})

        assert [frame for browser in browsers.values() for frame in sent_frames(browser)] == []

    async def test_refused_relay_logs_the_sender_and_never_the_target_the_browser_chose(
        self, link, room_manager, capsys
    ):
        _room, browsers = await seat_in_room(link, room_manager, "p1", "p2")
        sentinel = secrets.token_hex(16)

        with structlog.testing.capture_logs() as logs:
            await deliver(browsers["p1"], {"type": SignalType.OFFER.value, "target": sentinel, "sdp": "x"})

        [refusal] = [log for log in logs if log["event"] == "relay_outside_room"]
        assert set(refusal) == {"event", "peer_id", "log_level"}
        assert refusal["peer_id"] == browsers["p1"].peer_id
        assert sentinel not in repr(logs)
        captured = capsys.readouterr()
        assert sentinel not in captured.out + captured.err


class TestDisconnect:
    async def test_guest_leaving_tells_the_others_without_host_away_and_the_room_stays(self, link, room_manager):
        room, browsers = await seat_in_room(link, room_manager, "host1", "p1")

        await browsers["p1"].drop(1000)

        assert sent_frames(browsers["host1"]) == [
            {"type": SignalType.PARTICIPANT_LEFT.value, "participant_id": browsers["p1"].peer_id}
        ]
        assert room_manager.has_room(room.room_id)

    async def test_last_guest_leaving_keeps_the_room(self, link, room_manager):
        room, browsers = await seat_in_room(link, room_manager, "host1", "p1")
        await browsers["p1"].drop(1000)
        await browsers["host1"].drop(1000)
        assert room_manager.has_room(room.room_id)

    async def test_disconnect_without_room_is_safe(self, link, handler):
        browser = await connect(link, "c1")
        await browser.drop(1000)
        assert handler.seats_of("c1") == ()

    async def test_disconnect_from_deleted_room(self, link, handler, room_manager):
        room, browsers = await seat_in_room(link, room_manager, "p1")
        room_manager.remove_room(room.room_id)
        await browsers["p1"].drop(1000)
        assert handler.seats_of("p1") == ()


class TestPeerInNoRoom:
    @pytest.mark.parametrize(
        "message",
        [
            pytest.param({"type": SignalType.LOCK_ROOM.value}, id="lock"),
            pytest.param({"type": SignalType.UNLOCK_ROOM.value}, id="unlock"),
            pytest.param({"type": SignalType.END_ROOM.value}, id="end_room"),
            pytest.param({"type": SignalType.CHAT.value, "message": "hello"}, id="chat"),
            pytest.param({"type": SignalType.MEDIA_STATE.value, "audio": True, "video": True}, id="media_state"),
        ],
    )
    async def test_room_request_from_a_peer_in_no_room_is_ignored(self, link, message):
        browser = await connect(link, "c1")

        await deliver(browser, message)

        assert browser.socket.sent == []


class TestLockUnlock:
    async def test_host_can_lock_room(self, link, room_manager):
        room, browsers = await seat_in_room(link, room_manager, "host1", "p1")

        await deliver(browsers["host1"], {"type": SignalType.LOCK_ROOM.value})

        assert sent_types(browsers["host1"]) == [SignalType.ROOM_LOCKED.value]
        assert sent_types(browsers["p1"]) == [SignalType.ROOM_LOCKED.value]
        assert room.locked is True

    async def test_host_can_unlock_room(self, link, room_manager):
        room, browsers = await seat_in_room(link, room_manager, "host1")
        room_manager.set_room_locked(room.room_id, account_named("host1"), locked=True)

        await deliver(browsers["host1"], {"type": SignalType.UNLOCK_ROOM.value})

        assert sent_types(browsers["host1"]) == [SignalType.ROOM_UNLOCKED.value]
        assert room.locked is False


class TestSeatsOf:
    async def test_lists_the_seats_of_one_account_a_parked_one_until_its_window_ends(
        self, link, handler, room_manager, scheduler
    ):
        room, _ = await seat_in_room(link, room_manager, "host1", "ben")
        guest = FakeSocket(
            [text_frame(join_message(room.room_id)), disconnect_frame(1006)], account=account_named("anna")
        )
        await handler.handle_websocket(guest)

        [parked] = handler.seats_of("anna")
        assert (parked.call_id, parked.host, parked.replay_frames) == (room.room_id, False, len(guest.sent))
        assert [(seat.call_id, seat.host) for seat in handler.seats_of("host1")] == [(room.room_id, True)]
        assert [(seat.call_id, seat.host) for seat in handler.seats_of("ben")] == [(room.room_id, False)]

        await scheduler.advance(RESUME_WINDOW_SECONDS)

        assert handler.seats_of("anna") == ()

    async def test_lists_a_seat_that_has_not_joined_a_call_without_a_call_id(self, link, handler):
        await connect(link, "anna")

        assert [(seat.call_id, seat.host) for seat in handler.seats_of("anna")] == [(None, False)]


class TestHandleWebsocket:
    async def test_abnormal_disconnect_is_logged(self, handler, room_manager):
        socket = FakeSocket([text_frame({"type": SignalType.JOIN.value}), disconnect_frame(1006)])
        with structlog.testing.capture_logs() as logs:
            await handler.handle_websocket(socket)

        [error_log] = [log for log in logs if log["event"] == "websocket_error"]
        assert error_log["code"] == 1006
        assert room_manager.has_room(socket.sent[0]["room_id"]) is True

    @pytest.mark.parametrize("code", [1000, 1001])
    async def test_normal_disconnect_is_not_logged_as_error(self, handler, code):
        socket = FakeSocket([disconnect_frame(code)])
        with structlog.testing.capture_logs() as logs:
            await handler.handle_websocket(socket)

        assert [log for log in logs if log["event"] == "websocket_error"] == []

    async def test_binary_frame_is_ignored_and_later_text_frames_still_process(self, handler, room_manager):
        socket = FakeSocket(
            [
                {"type": "websocket.receive", "bytes": b"x"},
                text_frame({"type": SignalType.JOIN.value}),
                disconnect_frame(1000),
            ]
        )
        await handler.handle_websocket(socket)
        assert room_manager.has_room(socket.sent[0]["room_id"]) is True

    async def test_refused_handshake_closes_with_policy_violation_without_accepting(self, room_manager):
        refusing_handler = SignalingHandler(room_manager, SocketAccountResolver(), ManualScheduler(), FakeGameMaster())
        socket = FakeSocket([], account=None)
        await refusing_handler.handle_websocket(socket)
        assert socket.closed_with == 1008
        assert not socket.accepted

    async def test_failing_resolver_propagates_without_accepting_the_socket(self, room_manager):
        failing_handler = SignalingHandler(room_manager, RaisingIdentityResolver(), ManualScheduler(), FakeGameMaster())
        socket = FakeSocket([])
        with pytest.raises(ResolverError):
            await failing_handler.handle_websocket(socket)
        assert not socket.accepted

    async def test_admitted_handshake_accepts_and_registers_a_peer_carrying_the_account(self, handler, room_manager):
        socket = FakeSocket([text_frame(join_message()), disconnect_frame(1000)])

        await handler.handle_websocket(socket)

        assert socket.accepted
        assert room_manager.get_room(socket.sent[0]["room_id"]).host == ADMITTED_ACCOUNT

    async def test_two_sockets_of_one_account_become_two_peers(self, handler):
        sockets = [FakeSocket([text_frame(join_message()), disconnect_frame(1000)]) for _ in range(2)]

        for socket in sockets:
            await handler.handle_websocket(socket)

        first, second = (socket.sent[0]["participant_id"] for socket in sockets)
        assert first != second


class TestChat:
    async def test_chat_broadcasts_to_other_participants(self, link, room_manager):
        _room, browsers = await seat_in_room(link, room_manager, "host1", "p1")

        await deliver(browsers["host1"], {"type": SignalType.CHAT.value, "message": "hello"})

        assert sent_frames(browsers["p1"]) == [
            {"type": SignalType.CHAT.value, "message": "hello", "from": browsers["host1"].peer_id}
        ]
        assert browsers["host1"].socket.sent == []

    async def test_chat_empty_message_is_ignored(self, link, room_manager):
        _room, browsers = await seat_in_room(link, room_manager, "host1", "p1")

        await deliver(browsers["host1"], {"type": SignalType.CHAT.value, "message": ""})

        assert browsers["p1"].socket.sent == []


class TestMediaState:
    async def test_media_state_reaches_every_other_participant_with_from(self, link, room_manager):
        _, browsers = await seat_in_room(link, room_manager, "host1", "p1", "p2")

        await deliver(browsers["p1"], {"type": SignalType.MEDIA_STATE.value, "audio": False, "video": True})

        expected = {
            "type": SignalType.MEDIA_STATE.value,
            "from": browsers["p1"].peer_id,
            "audio": False,
            "video": True,
        }
        assert sent_frames(browsers["host1"]) == [expected]
        assert sent_frames(browsers["p2"]) == [expected]
        assert browsers["p1"].socket.sent == []

    @pytest.mark.parametrize(
        "state",
        [
            pytest.param({"audio": "no", "video": True}, id="audio_not_boolean"),
            pytest.param({"audio": True, "video": 1}, id="video_not_boolean"),
            pytest.param({"audio": True}, id="video_missing"),
            pytest.param({}, id="both_missing"),
        ],
    )
    async def test_media_state_without_boolean_audio_and_video_is_answered_invalid_message(
        self, link, room_manager, state
    ):
        _, browsers = await seat_in_room(link, room_manager, "host1", "p1")

        await deliver(browsers["p1"], {"type": SignalType.MEDIA_STATE.value, **state})

        assert [frame["reason"] for frame in sent_frames(browsers["p1"])] == [ErrorReason.INVALID_MESSAGE.value]
        assert browsers["host1"].socket.sent == []


class TestSend:
    async def test_send_to_unknown_client_is_safe_and_the_connected_one_is_still_served(self, link, room_manager):
        room, browsers = await seat_in_room(link, room_manager, "host1", "c1")
        room_manager.add_participant(room.room_id, "unknown", account_named("unknown"))

        await deliver(browsers["host1"], {"type": SignalType.CHAT.value, "message": "test"})

        assert sent_types(browsers["c1"]) == [SignalType.CHAT.value]
        assert browsers["host1"].socket.sent == []

    @pytest.mark.parametrize("closed_state", ["client_state", "application_state"])
    async def test_send_to_a_peer_whose_socket_is_closed_sends_nothing(self, link, room_manager, closed_state):
        _room, browsers = await seat_in_room(link, room_manager, "host1", "c1")
        setattr(browsers["c1"].socket, closed_state, WebSocketState.DISCONNECTED)

        await deliver(browsers["host1"], {"type": SignalType.CHAT.value, "message": "test"})

        assert browsers["c1"].socket.sent == []


class TestSendToGonePeer:
    async def test_relay_to_peer_whose_send_raises_disconnect_keeps_the_sender_running(self, link, room_manager):
        _room, browsers = await seat_in_room(link, room_manager, "p1", "p2")
        sender, gone = browsers["p1"], browsers["p2"]
        gone.socket.send_text = raise_disconnect

        await deliver(sender, {"type": SignalType.OFFER.value, "target": gone.peer_id})
        await deliver(sender, "not json")

        assert sender.socket.sent[-1]["reason"] == ErrorReason.INVALID_MESSAGE.value

    async def test_relay_to_missing_targets_through_handle_websocket_sends_the_sender_no_frame(self, handler):
        socket = FakeSocket(
            [
                text_frame({"type": SignalType.OFFER.value, "target": ""}, seq=1),
                text_frame({"type": SignalType.OFFER.value, "target": "nobody"}, seq=2),
                disconnect_frame(1000),
            ]
        )
        await handler.handle_websocket(socket)
        assert socket.sent == []

    async def test_relay_through_handle_websocket_to_vanished_peer_still_answers_later_frames(self, link, handler):
        gone = await link.join("gone")
        gone.socket.send_text = raise_disconnect
        socket = FakeSocket(
            [
                text_frame(join_message(gone.sockets[0].sent[0]["room_id"])),
                text_frame({"type": SignalType.OFFER.value, "target": gone.peer_id}, seq=1),
                {"type": "websocket.receive", "text": "not json"},
                disconnect_frame(1000),
            ]
        )
        await handler.handle_websocket(socket)
        errors = [reply["reason"] for reply in socket.sent if reply["type"] == SignalType.ERROR.value]
        assert errors == [ErrorReason.INVALID_MESSAGE.value]

    async def test_send_over_a_transport_that_fails_is_a_no_op_via_starlette_translation(self, handler):
        inbound = [
            {"type": "websocket.connect"},
            text_frame(join_message()),
            {"type": "websocket.disconnect", "code": 1000},
        ]

        async def receive():
            return inbound.pop(0)

        async def failing_send(message):
            if message["type"] == "websocket.accept":
                return
            raise OSError("transport closed")

        scope = {"type": "websocket", "path": "/ws", "headers": [], "query_string": b""}
        websocket = WebSocket(scope, receive, failing_send)
        websocket.account = ADMITTED_ACCOUNT

        with structlog.testing.capture_logs() as logs:
            await handler.handle_websocket(websocket)

        assert [log["event"] for log in logs if log["event"] == "send_to_gone_peer"] == ["send_to_gone_peer"]


def join_message(room_id=None):
    return {"type": SignalType.JOIN.value, "room_id": room_id}


def frames_of_type(browser, signal_type):
    return [frame for frame in sent_frames(browser) if frame["type"] == signal_type.value]


async def drop_host(link, room_manager, *guests):
    room, browsers = await seat_in_room(link, room_manager, "host1", *guests)
    await browsers["host1"].drop(1000)
    return room, browsers


class TestHostGrace:
    async def test_last_host_connection_dropping_sends_host_away_then_participant_left_and_keeps_the_room(
        self, link, room_manager
    ):
        room, browsers = await drop_host(link, room_manager, "p1")

        host_id = browsers["host1"].peer_id
        assert sent_frames(browsers["p1"]) == [
            {"type": SignalType.HOST_AWAY.value, "seconds_left": 60, "participant_id": host_id},
            {"type": SignalType.PARTICIPANT_LEFT.value, "participant_id": host_id},
        ]
        assert room_manager.has_room(room.room_id)

    async def test_room_survives_until_the_grace_runs_out_then_closes_for_every_peer_as_host_gone(
        self, link, room_manager, scheduler
    ):
        room, browsers = await drop_host(link, room_manager, "p1", "p2")

        await scheduler.advance(59)
        assert room_manager.has_room(room.room_id)
        await scheduler.advance(1)

        closed = {
            "type": SignalType.ROOM_CLOSED.value,
            "reason": RoomClosedReason.HOST_GONE.value,
            "grace_seconds": HOST_GRACE_SECONDS,
        }
        assert frames_of_type(browsers["p1"], SignalType.ROOM_CLOSED) == [closed]
        assert frames_of_type(browsers["p2"], SignalType.ROOM_CLOSED) == [closed]
        assert not room_manager.has_room(room.room_id)

    async def test_former_member_of_a_closed_room_cannot_lock_or_chat(self, link, room_manager, scheduler):
        _room, browsers = await drop_host(link, room_manager, "p1", "p2")
        await scheduler.advance(60)
        frames_before = {name: len(sent_frames(browser)) for name, browser in browsers.items()}

        await deliver(browsers["p1"], {"type": SignalType.LOCK_ROOM.value})
        await deliver(browsers["p1"], {"type": SignalType.CHAT.value, "message": "hi"})

        assert {name: len(sent_frames(browser)) for name, browser in browsers.items()} == frames_before

    async def test_room_of_a_host_who_was_alone_closes_when_the_grace_runs_out(self, link, room_manager, scheduler):
        room, _browsers = await drop_host(link, room_manager)

        assert room_manager.has_room(room.room_id)
        await scheduler.advance(60)

        assert not room_manager.has_room(room.room_id)

    async def test_host_account_rejoining_within_the_grace_is_host_again_and_the_room_stays(
        self, link, room_manager, scheduler
    ):
        room, browsers = await drop_host(link, room_manager, "p1")
        await scheduler.advance(30)

        returning = await link.join("host1", room.room_id)
        await scheduler.advance(60)

        [existing] = frames_of_type(returning, SignalType.EXISTING_PARTICIPANTS)
        assert existing["is_host"] is True
        assert sent_frames(browsers["p1"])[-2:] == [
            {"type": SignalType.PARTICIPANT_JOINED.value, "participant_id": returning.peer_id, "name": "host1"},
            {"type": SignalType.HOST_BACK.value, "participant_id": returning.peer_id},
        ]
        assert frames_of_type(browsers["p1"], SignalType.ROOM_CLOSED) == []
        assert room_manager.has_room(room.room_id)

    async def test_host_who_was_alone_finds_the_room_on_reload(self, link, room_manager, scheduler):
        room, _browsers = await drop_host(link, room_manager)

        returning = await link.join("host1", room.room_id)
        await scheduler.advance(60)

        assert frames_of_type(returning, SignalType.EXISTING_PARTICIPANTS)[0]["is_host"] is True
        assert room_manager.has_room(room.room_id)

    async def test_host_account_is_admitted_past_the_lock_during_the_grace(self, link, room_manager):
        room, browsers = await drop_host(link, room_manager, "p1")
        room.locked = True

        returning = await link.join("host1", room.room_id)

        assert frames_of_type(returning, SignalType.EXISTING_PARTICIPANTS)[0]["locked"] is True
        assert frames_of_type(browsers["p1"], SignalType.HOST_BACK)

    async def test_host_account_returns_into_its_reserved_seat_of_an_otherwise_full_room(self, link, room_manager):
        guests = [f"g{index}" for index in range(ROOM_LIMIT - 1)]
        room, _browsers = await drop_host(link, room_manager, *guests)
        stranger = await connect(link, "stranger")
        await deliver(stranger, join_message(room.room_id))

        returning = await link.join("host1", room.room_id)

        assert frames_of_type(stranger, SignalType.ERROR)[0]["reason"] == ErrorReason.FULL.value
        assert frames_of_type(returning, SignalType.EXISTING_PARTICIPANTS)

    async def test_joiner_during_the_grace_gets_host_away_with_the_seconds_left_after_the_existing_list(
        self, link, room_manager, scheduler
    ):
        room, browsers = await drop_host(link, room_manager, "p1")
        await scheduler.advance(20)

        joiner = await connect(link, "p2")
        await joiner.send_raw(join_message(room.room_id))

        assert sent_types(joiner) == [SignalType.EXISTING_PARTICIPANTS.value, SignalType.HOST_AWAY.value]
        assert frames_of_type(joiner, SignalType.HOST_AWAY)[0]["seconds_left"] == 40
        assert frames_of_type(browsers["p1"], SignalType.HOST_BACK) == []

    async def test_host_with_two_connections_keeps_the_room_hosted_when_one_drops(self, link, room_manager):
        room, browsers = await seat_in_room(link, room_manager, "host1", "p1")
        second = await link.join("host1", room.room_id)

        await browsers["host1"].drop(1000)
        await deliver(second, {"type": SignalType.LOCK_ROOM.value})

        assert frames_of_type(browsers["p1"], SignalType.HOST_AWAY) == []
        assert frames_of_type(second, SignalType.ROOM_LOCKED)
        assert frames_of_type(second, SignalType.EXISTING_PARTICIPANTS)[0]["is_host"] is True

    @pytest.mark.parametrize("signal_type", [SignalType.LOCK_ROOM, SignalType.UNLOCK_ROOM])
    async def test_lock_and_unlock_during_the_grace_answer_not_host_and_leave_the_lock_alone(
        self, link, room_manager, signal_type
    ):
        room, browsers = await drop_host(link, room_manager, "p1")

        await deliver(browsers["p1"], {"type": signal_type.value})

        assert frames_of_type(browsers["p1"], SignalType.ERROR)[0]["reason"] == ErrorReason.NOT_HOST.value
        assert room.locked is False


class TestEndRoom:
    async def test_host_ending_the_room_closes_it_as_ended_for_every_peer_including_the_host(self, link, room_manager):
        room, browsers = await seat_in_room(link, room_manager, "host1", "p1")

        await deliver(browsers["host1"], {"type": SignalType.END_ROOM.value})

        closed = {"type": SignalType.ROOM_CLOSED.value, "reason": RoomClosedReason.ENDED.value}
        assert frames_of_type(browsers["host1"], SignalType.ROOM_CLOSED) == [closed]
        assert frames_of_type(browsers["p1"], SignalType.ROOM_CLOSED) == [closed]
        assert not room_manager.has_room(room.room_id)


class TestExpiryRace:
    async def test_expiry_after_the_host_returned_does_nothing(self, link, room_manager, scheduler):
        room, browsers = await seat_in_room(link, room_manager, "host1", "p1")
        await scheduler.advance(10)
        await browsers["host1"].drop(1000)
        [grace_timer] = [timer for timer in scheduler.pending_timers() if timer.deadline == 10 + HOST_GRACE_SECONDS]
        await link.join("host1", room.room_id)

        await grace_timer.callback()

        assert frames_of_type(browsers["p1"], SignalType.ROOM_CLOSED) == []
        assert room_manager.has_room(room.room_id)
