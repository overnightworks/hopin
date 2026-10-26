from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
import structlog
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from webauth.config import installed_web_auth_config

from hopin.app import create_app
from hopin.auth.stores import SignInStores
from hopin.config.constants import (
    AUTH_CALLBACK_PATH,
    AUTH_START_PATH,
    HOST_GRACE_SECONDS,
    LOGIN_GROUP,
    MAX_LIVE_SEATS_PER_ACCOUNT,
    SEAT_CLOSED_BY_SERVER_CODE,
    SESSION_ENDED_CLOSE_CODE,
    WEBSOCKET_PATH,
    ErrorReason,
    RoomClosedReason,
    SignalType,
)
from tests.fake_issuer import FakeIssuer
from tests.sign_in_settings import TEST_PUBLIC_ORIGIN
from tests.signed_in import SeededMember, cookie_header, handshake_headers, parsed_set_cookies, seed_member

if TYPE_CHECKING:
    from collections.abc import Callable

POLICY_VIOLATION_CODE = 1008


@pytest.fixture
def stores():
    return SignInStores.in_memory()


@pytest.fixture
def app(sign_in_settings, stores):
    settings = sign_in_settings(max_participants_per_room=3).settings
    return create_app(settings, stores=stores)


@pytest.fixture
def client(app):
    with TestClient(app) as shared_portal_client:
        yield shared_portal_client


@pytest.fixture
def seed(app, stores) -> Callable[..., SeededMember]:
    config = installed_web_auth_config(app)
    return lambda **options: seed_member(stores, config, **options)


@pytest.fixture
def member_named(seed) -> Callable[[str], SeededMember]:
    """The same label is the same member, so a reload or a second socket stays the same account."""
    members: dict[str, SeededMember] = {}

    def member(label: str) -> SeededMember:
        if label not in members:
            members[label] = seed()
        return members[label]

    return member


@pytest.fixture
def logs(client):
    with structlog.testing.capture_logs() as captured:
        yield captured


def refused_with_code(client, headers, path=WEBSOCKET_PATH):
    with pytest.raises(WebSocketDisconnect) as refused, client.websocket_connect(path, headers=headers):
        pass
    return refused.value.code


def events_named(logs, event):
    return [entry for entry in logs if entry["event"] == event]


def expires_at(stores, member):
    stored = stores.sessions.load(member.session.id)
    assert stored is not None
    return stored.expires_at


def joined_room(client, member):
    with client.websocket_connect(WEBSOCKET_PATH, headers=handshake_headers(member)) as ws:
        ws.send_json({"type": SignalType.JOIN.value})
        return ws.receive_json()


def number_frames_after_the_first(websocket):
    """Make send_json number each frame after the socket's first, as the browser does."""
    send_unnumbered = websocket.send_json
    sent = 0

    def send_json(data):
        nonlocal sent
        send_unnumbered(data if sent == 0 else {**data, "seq": sent})
        sent += 1

    websocket.send_json = send_json
    return websocket


def without_seq(frame):
    return {key: value for key, value in frame.items() if key != "seq"}


@pytest.fixture
def connect(client, member_named):
    with ExitStack() as sessions:
        yield lambda account: number_frames_after_the_first(
            sessions.enter_context(
                client.websocket_connect(WEBSOCKET_PATH, headers=handshake_headers(member_named(account)))
            )
        )


def open_room(connect, account):
    ws = connect(account)
    ws.send_json({"type": SignalType.JOIN.value})
    return ws, ws.receive_json()["room_id"]


def join_room(connect, account, room_id):
    ws = connect(account)
    ws.send_json({"type": SignalType.JOIN.value, "room_id": room_id})
    return ws, ws.receive_json()


@pytest.mark.integration
class TestConnect:
    def test_connect_without_a_session_is_refused_at_the_handshake_and_logs_only_the_reason(self, client, logs):
        code = refused_with_code(client, handshake_headers(None), "/ws?id=someone")

        assert code == POLICY_VIOLATION_CODE
        assert [entry for entry in logs if entry["event"] != "handshake_refused"] == []
        assert events_named(logs, "handshake_refused") == [
            {"event": "handshake_refused", "reason": "no-session", "log_level": "info"}
        ]

    def test_connect_after_the_session_ended_is_refused(self, client, stores, seed, logs):
        member = seed()
        stores.sessions.delete(member.session.id)

        assert refused_with_code(client, handshake_headers(member)) == POLICY_VIOLATION_CODE
        assert events_named(logs, "handshake_refused")[0]["reason"] == "session-ended-or-expired"

    @pytest.mark.parametrize(
        ("origin", "reason"),
        [
            pytest.param(None, "missing-origin", id="no_origin"),
            pytest.param(f"{TEST_PUBLIC_ORIGIN}.evil.example", "foreign-origin", id="foreign_origin"),
        ],
    )
    def test_connect_from_a_page_that_is_not_hopins_is_refused_without_renewing_the_session(
        self, client, stores, seed, logs, origin, reason
    ):
        member = seed(expires_in=timedelta(hours=1))
        before = expires_at(stores, member)

        assert refused_with_code(client, handshake_headers(member, origin)) == POLICY_VIOLATION_CODE

        assert events_named(logs, "handshake_refused")[0]["reason"] == reason
        assert expires_at(stores, member) == before

    def test_connect_with_a_session_admits_the_member_as_its_account_and_renews_the_session(
        self, client, stores, seed, logs
    ):
        member = seed(expires_in=timedelta(hours=1))
        before = expires_at(stores, member)

        room = joined_room(client, member)

        assert (room["type"], room["is_host"]) == (SignalType.ROOM_CREATED.value, True)
        [connected] = events_named(logs, "client_connected")
        assert "account" not in connected
        assert expires_at(stores, member) > before

    def test_query_names_nobody_so_another_accounts_id_does_not_make_the_socket_host(self, client, member_named):
        host = member_named("host")
        with client.websocket_connect(WEBSOCKET_PATH, headers=handshake_headers(host)) as host_ws:
            host_ws.send_json({"type": SignalType.JOIN.value})
            room_id = host_ws.receive_json()["room_id"]
            host_path = f"{WEBSOCKET_PATH}?id={host.account.id}"
            with client.websocket_connect(host_path, headers=handshake_headers(member_named("a"))) as ws:
                ws.send_json({"type": SignalType.JOIN.value, "room_id": room_id})
                existing = ws.receive_json()
                ws.send_json({"type": SignalType.LOCK_ROOM.value, "seq": 1})
                refusal = ws.receive_json()
            with client.websocket_connect(WEBSOCKET_PATH, headers=handshake_headers(member_named("c"))) as ws:
                ws.send_json({"type": SignalType.JOIN.value, "room_id": room_id})
                admitted = ws.receive_json()

        assert existing["is_host"] is False
        assert refusal["reason"] == ErrorReason.NOT_HOST.value
        assert admitted["type"] == SignalType.EXISTING_PARTICIPANTS.value

    @pytest.mark.parametrize("scenario", ["refused", "admitted"])
    def test_logs_carry_no_username_cookie_or_session_id(self, client, stores, seed, logs, scenario):
        member = seed()
        if scenario == "refused":
            stores.sessions.delete(member.session.id)
            refused_with_code(client, handshake_headers(member))
        else:
            joined_room(client, member)

        cookie_values = [morsel.value for _, morsel in parsed_set_cookies(member.set_cookie_headers)]
        secrets_in_logs = [member.account.username, member.session.id, cookie_header(member.set_cookie_headers)]
        assert [secret for secret in [*secrets_in_logs, *cookie_values] if secret in str(logs)] == []
        permitted_fields = {
            "refused": ("handshake_refused", {"event", "reason", "log_level"}),
            "admitted": ("client_connected", {"event", "peer_id", "log_level"}),
        }
        event, fields = permitted_fields[scenario]
        assert [set(entry) for entry in events_named(logs, event)] == [fields]

    def test_logs_of_a_refused_recheck_carry_no_account_username_cookie_or_session_id(self, client, stores, seed, logs):
        member = seed()
        with client.websocket_connect(WEBSOCKET_PATH, headers=handshake_headers(member)) as ws:
            ws.send_json({"type": SignalType.JOIN.value})
            ws.receive_json()
            stores.sessions.delete(member.session.id)
            ws.send_json({"type": SignalType.CHAT.value, "seq": 1, "message": "hello"})
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()

        cookie_values = [morsel.value for _, morsel in parsed_set_cookies(member.set_cookie_headers)]
        secrets_in_logs = [member.account.username, member.account.id, member.session.id]
        assert [secret for secret in [*secrets_in_logs, *cookie_values] if secret in str(logs)] == []
        assert [set(entry) for entry in events_named(logs, "handshake_refused")] == [{"event", "reason", "log_level"}]
        assert [set(entry) for entry in events_named(logs, "session_ended")] == [{"event", "peer_id", "log_level"}]


@pytest.mark.integration
class TestSessionRecheck:
    def test_guest_whose_session_ended_is_closed_before_its_next_frame_reaches_the_host(
        self, connect, stores, member_named
    ):
        host_ws, room_id = open_room(connect, "host")
        guest_ws, _ = join_room(connect, "guest", room_id)
        guest_peer_id = host_ws.receive_json()["participant_id"]
        stores.sessions.delete(member_named("guest").session.id)

        guest_ws.send_json({"type": SignalType.MEDIA_STATE.value, "seq": 1, "audio": False, "video": True})

        with pytest.raises(WebSocketDisconnect) as closed:
            guest_ws.receive_json()
        assert closed.value.code == SESSION_ENDED_CLOSE_CODE
        left = host_ws.receive_json()
        assert (left["type"], left["participant_id"]) == (SignalType.PARTICIPANT_LEFT.value, guest_peer_id)
        _, joined = join_room(connect, "late", room_id)
        assert joined["type"] != SignalType.ERROR.value

    def test_member_whose_session_expired_is_closed_at_its_next_frame(self, connect, stores, member_named):
        ws, _ = open_room(connect, "host")
        expired = datetime.now(UTC) - timedelta(hours=1)
        stores.sessions.touch(member_named("host").session.id, expires_at=expired)

        ws.send_json({"type": "no-such-signal", "seq": 1})

        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == SESSION_ENDED_CLOSE_CODE

    def test_frame_of_a_live_member_renews_the_session_never_past_its_absolute_cap(self, connect, stores, member_named):
        ws, _ = open_room(connect, "host")
        member = member_named("host")
        absolute_cap = member.session.expires_at
        shortened = absolute_cap - timedelta(hours=3)
        stores.sessions.touch(member.session.id, expires_at=shortened)

        ws.send_json({"type": "no-such-signal", "seq": 1})
        assert ws.receive_json()["type"] == SignalType.ERROR.value

        renewed = expires_at(stores, member)
        assert shortened < renewed <= absolute_cap


@pytest.mark.integration
class TestProductionWiring:
    def test_member_signed_in_through_the_issuer_opens_the_socket_of_an_app_with_default_stores(self, sign_in_settings):
        sign_in = sign_in_settings()
        issuer = FakeIssuer(sign_in)
        app = create_app(sign_in.settings, issuer_transports=issuer.transports)
        browser = TestClient(app, base_url=TEST_PUBLIC_ORIGIN, follow_redirects=False)
        start = browser.get(AUTH_START_PATH)
        query = issuer.authorize(start.headers["location"], subject="subject", groups=[LOGIN_GROUP], username="alice")
        callback = browser.get(f"{AUTH_CALLBACK_PATH}?{query}")
        headers = {"origin": TEST_PUBLIC_ORIGIN, "cookie": cookie_header(callback.headers.get_list("set-cookie"))}

        with browser.websocket_connect(WEBSOCKET_PATH, headers=headers) as ws:
            ws.send_json({"type": SignalType.JOIN.value})
            room = ws.receive_json()

        assert (room["type"], room["is_host"]) == (SignalType.ROOM_CREATED.value, True)


@pytest.mark.integration
class TestRoomCreation:
    def test_create_room_returns_room_id(self, connect):
        ws = connect("host1")
        ws.send_json({"type": SignalType.JOIN.value})
        response = ws.receive_json()
        assert response["type"] == SignalType.ROOM_CREATED.value
        assert "room_id" in response
        assert response["is_host"] is True

    def test_create_room_ignores_a_password_key(self, connect):
        ws = connect("host1")
        ws.send_json({"type": SignalType.JOIN.value, "password": "secret"})
        response = ws.receive_json()
        assert response["type"] == SignalType.ROOM_CREATED.value
        assert response["is_host"] is True


@pytest.mark.integration
class TestJoining:
    def test_join_existing_room(self, connect):
        host_ws = connect("host1")
        host_ws.send_json({"type": SignalType.JOIN.value})
        created = host_ws.receive_json()
        room_id = created["room_id"]

        p1_ws = connect("p1")
        p1_ws.send_json({"type": SignalType.JOIN.value, "room_id": room_id})

        p1_response = p1_ws.receive_json()
        assert p1_response["type"] == SignalType.EXISTING_PARTICIPANTS.value
        [host_peer_id] = p1_response["participants"]
        assert host_peer_id not in {"host1", "p1"}
        assert p1_response["is_host"] is False

        host_notification = host_ws.receive_json()
        assert host_notification["type"] == SignalType.PARTICIPANT_JOINED.value
        assert host_notification["participant_id"] not in {"host1", "p1", host_peer_id}

    def test_names_the_others_to_the_joiner_and_the_joiner_to_the_others(self, connect, member_named):
        host_ws, room_id = open_room(connect, "a")
        _p1_ws, existing = join_room(connect, "b", room_id)

        [host_peer_id] = existing["participants"]
        joined = host_ws.receive_json()
        assert existing["names"] == {host_peer_id: member_named("a").account.username}
        assert joined["name"] == member_named("b").account.username

    def test_names_the_same_account_on_a_second_device_under_one_username(self, connect, member_named):
        _first_ws, room_id = open_room(connect, "a")
        _second_ws, existing = join_room(connect, "a", room_id)

        assert list(existing["names"].values()) == [member_named("a").account.username]

    def test_join_nonexistent_room_sends_error(self, connect):
        ws = connect("p1")
        ws.send_json({"type": SignalType.JOIN.value, "room_id": "nonexistent"})
        response = ws.receive_json()
        assert response["type"] == SignalType.ERROR.value
        assert response["reason"] == ErrorReason.NOT_FOUND.value

    def test_full_room_sends_error(self, connect):
        host_ws = connect("host1")
        host_ws.send_json({"type": SignalType.JOIN.value})
        created = host_ws.receive_json()
        room_id = created["room_id"]

        p1_ws = connect("p1")
        p1_ws.send_json({"type": SignalType.JOIN.value, "room_id": room_id})
        p1_ws.receive_json()
        host_ws.receive_json()

        p2_ws = connect("p2")
        p2_ws.send_json({"type": SignalType.JOIN.value, "room_id": room_id})
        p2_ws.receive_json()
        host_ws.receive_json()
        p1_ws.receive_json()

        p3_ws = connect("p3")
        p3_ws.send_json({"type": SignalType.JOIN.value, "room_id": room_id})
        response = p3_ws.receive_json()
        assert response["type"] == SignalType.ERROR.value
        assert response["reason"] == ErrorReason.FULL.value
        assert response["capacity"] == 3


@pytest.mark.integration
class TestRelay:
    def test_offer_relayed_between_peers(self, connect):
        host_ws = connect("host1")
        host_ws.send_json({"type": SignalType.JOIN.value})
        created = host_ws.receive_json()
        room_id = created["room_id"]

        p1_ws, existing = join_room(connect, "p1", room_id)
        [host_peer_id] = existing["participants"]
        p1_peer_id = host_ws.receive_json()["participant_id"]

        p1_ws.send_json(
            {
                "type": SignalType.OFFER.value,
                "target": host_peer_id,
                "sdp": "test_sdp_offer",
            },
        )

        relayed = host_ws.receive_json()
        assert relayed["type"] == SignalType.OFFER.value
        assert relayed["sdp"] == "test_sdp_offer"
        assert relayed["from"] == p1_peer_id


@pytest.mark.integration
class TestLockUnlock:
    def test_lock_prevents_joining(self, connect):
        host_ws = connect("host1")
        host_ws.send_json({"type": SignalType.JOIN.value})
        created = host_ws.receive_json()
        room_id = created["room_id"]

        host_ws.send_json({"type": SignalType.LOCK_ROOM.value})
        lock_msg = host_ws.receive_json()
        assert lock_msg["type"] == SignalType.ROOM_LOCKED.value

        p1_ws = connect("p1")
        p1_ws.send_json({"type": SignalType.JOIN.value, "room_id": room_id})
        response = p1_ws.receive_json()
        assert response["type"] == SignalType.ERROR.value
        assert response["reason"] == ErrorReason.LOCKED.value

    def test_unlock_allows_joining(self, connect):
        host_ws = connect("host1")
        host_ws.send_json({"type": SignalType.JOIN.value})
        created = host_ws.receive_json()
        room_id = created["room_id"]

        host_ws.send_json({"type": SignalType.LOCK_ROOM.value})
        host_ws.receive_json()

        host_ws.send_json({"type": SignalType.UNLOCK_ROOM.value})
        unlock_msg = host_ws.receive_json()
        assert unlock_msg["type"] == SignalType.ROOM_UNLOCKED.value

        p1_ws = connect("p1")
        p1_ws.send_json({"type": SignalType.JOIN.value, "room_id": room_id})
        response = p1_ws.receive_json()
        assert response["type"] == SignalType.EXISTING_PARTICIPANTS.value


@pytest.mark.integration
class TestDisconnect:
    def test_disconnect_notifies_remaining_peers(self, connect):
        host_ws = connect("host1")
        host_ws.send_json({"type": SignalType.JOIN.value})
        created = host_ws.receive_json()
        room_id = created["room_id"]

        p1_ws, _existing = join_room(connect, "p1", room_id)
        p1_peer_id = host_ws.receive_json()["participant_id"]

        p1_ws.close()

        left_msg = host_ws.receive_json()
        assert left_msg["type"] == SignalType.PARTICIPANT_LEFT.value
        assert left_msg["participant_id"] == p1_peer_id


@pytest.mark.integration
class TestHostGrace:
    def test_host_socket_closing_sends_the_guest_host_away_then_participant_left(self, connect):
        host_ws, room_id = open_room(connect, "host1")
        guest_ws, existing = join_room(connect, "guest", room_id)
        [host_peer_id] = existing["participants"]
        host_ws.receive_json()

        host_ws.close()

        away = guest_ws.receive_json()
        assert (away["type"], away["participant_id"]) == (SignalType.HOST_AWAY.value, host_peer_id)
        assert 0 < away["seconds_left"] <= HOST_GRACE_SECONDS
        left = guest_ws.receive_json()
        assert (left["type"], left["participant_id"]) == (SignalType.PARTICIPANT_LEFT.value, host_peer_id)

    def test_new_socket_of_the_host_account_is_host_again_and_the_guest_hears_host_back(self, connect):
        host_ws, room_id = open_room(connect, "host1")
        guest_ws, _existing = join_room(connect, "guest", room_id)
        host_ws.receive_json()
        host_ws.close()
        guest_ws.receive_json()
        guest_ws.receive_json()

        reloaded_ws, reloaded = join_room(connect, "host1", room_id)

        assert reloaded["type"] == SignalType.EXISTING_PARTICIPANTS.value
        assert reloaded["is_host"] is True
        joined = guest_ws.receive_json()
        back = guest_ws.receive_json()
        assert joined["type"] == SignalType.PARTICIPANT_JOINED.value
        assert (back["type"], back["participant_id"]) == (SignalType.HOST_BACK.value, joined["participant_id"])
        reloaded_ws.send_json({"type": SignalType.LOCK_ROOM.value})
        assert reloaded_ws.receive_json()["type"] == SignalType.ROOM_LOCKED.value


@pytest.mark.integration
class TestEndRoom:
    def test_end_room_closes_it_for_both_peers_and_the_id_is_gone(self, connect):
        host_ws, room_id = open_room(connect, "host1")
        guest_ws, _existing = join_room(connect, "guest", room_id)
        host_ws.receive_json()

        host_ws.send_json({"type": SignalType.END_ROOM.value})

        expected = {"type": SignalType.ROOM_CLOSED.value, "reason": RoomClosedReason.ENDED.value}
        assert without_seq(host_ws.receive_json()) == expected
        assert without_seq(guest_ws.receive_json()) == expected
        _late_ws, refusal = join_room(connect, "late", room_id)
        assert refusal["reason"] == ErrorReason.NOT_FOUND.value

    def test_end_room_closes_the_guest_socket_and_a_chat_from_one_that_ignores_it_reaches_nobody(self, connect):
        host_ws, room_id = open_room(connect, "host1")
        guest_ws, _existing = join_room(connect, "guest", room_id)
        host_ws.receive_json()
        host_ws.send_json({"type": SignalType.END_ROOM.value})
        assert guest_ws.receive_json()["type"] == SignalType.ROOM_CLOSED.value
        assert host_ws.receive_json()["type"] == SignalType.ROOM_CLOSED.value

        guest_ws.send_json({"type": SignalType.CHAT.value, "message": "still here"})

        with pytest.raises(WebSocketDisconnect):
            guest_ws.receive_json()
        with pytest.raises(WebSocketDisconnect):
            host_ws.receive_json()


@pytest.mark.integration
class TestRelayStaysInTheRoom:
    def test_offer_aimed_at_a_peer_in_another_room_is_not_delivered(self, connect):
        intruder_ws, _intruder_room = open_room(connect, "intruder")
        victim_host_ws, victim_room = open_room(connect, "victim-host")
        victimguest_ws, existing = join_room(connect, "victim-guest", victim_room)
        [victim_host_peer_id] = existing["participants"]
        victim_host_ws.receive_json()

        intruder_ws.send_json({"type": SignalType.OFFER.value, "target": victim_host_peer_id, "sdp": "stolen"})
        intruder_ws.send_bytes(b"x")
        intruder_ws.send_text("not json")
        assert intruder_ws.receive_json()["reason"] == ErrorReason.INVALID_MESSAGE.value
        victimguest_ws.send_json({"type": SignalType.CHAT.value, "message": "inside the room"})

        assert victim_host_ws.receive_json()["type"] == SignalType.CHAT.value


@pytest.mark.integration
class TestNonTextFrames:
    def test_binary_frame_is_ignored_and_later_text_frames_still_process(self, connect):
        ws = connect("host1")

        ws.send_bytes(b"x")
        ws.send_json({"type": SignalType.JOIN.value})

        response = ws.receive_json()
        assert response["type"] == SignalType.ROOM_CREATED.value


@pytest.mark.integration
class TestChat:
    def test_chat_message_delivered_to_other_participants(self, connect):
        host_ws = connect("host1")
        host_ws.send_json({"type": SignalType.JOIN.value})
        created = host_ws.receive_json()
        room_id = created["room_id"]

        p1_ws, existing = join_room(connect, "p1", room_id)
        [host_peer_id] = existing["participants"]
        host_ws.receive_json()

        host_ws.send_json({"type": SignalType.CHAT.value, "message": "hello everyone"})

        chat_msg = p1_ws.receive_json()
        assert chat_msg["type"] == SignalType.CHAT.value
        assert chat_msg["message"] == "hello everyone"
        assert chat_msg["from"] == host_peer_id


@pytest.mark.integration
class TestMediaState:
    def test_media_state_reaches_the_guest_with_the_hosts_participant_id_as_from(self, connect):
        host_ws, room_id = open_room(connect, "host1")
        guest_ws, existing = join_room(connect, "p1", room_id)
        [host_peer_id] = existing["participants"]
        host_ws.receive_json()

        host_ws.send_json({"type": SignalType.MEDIA_STATE.value, "audio": False, "video": True})

        relayed = guest_ws.receive_json()
        assert (relayed["type"], relayed["from"], relayed["audio"], relayed["video"]) == (
            SignalType.MEDIA_STATE.value,
            host_peer_id,
            False,
            True,
        )


@pytest.mark.integration
class TestPeersAreServerMinted:
    def test_two_sockets_of_one_account_join_as_two_peers_that_reach_each_other(self, connect):
        first_ws, room_id = open_room(connect, "alice")
        second_ws, existing = join_room(connect, "alice", room_id)
        [first_peer_id] = existing["participants"]
        second_peer_id = first_ws.receive_json()["participant_id"]
        assert first_peer_id != second_peer_id

        second_ws.send_json({"type": SignalType.OFFER.value, "target": first_peer_id, "sdp": "from_second"})
        first_ws.send_json({"type": SignalType.OFFER.value, "target": second_peer_id, "sdp": "from_first"})

        to_first = first_ws.receive_json()
        to_second = second_ws.receive_json()
        assert (to_first["sdp"], to_first["from"]) == ("from_second", second_peer_id)
        assert (to_second["sdp"], to_second["from"]) == ("from_first", first_peer_id)

    def test_socket_naming_another_peers_minted_id_does_not_take_over_its_entry(self, connect):
        host_ws, room_id = open_room(connect, "host1")
        guest_ws, existing = join_room(connect, "guest", room_id)
        [host_peer_id] = existing["participants"]
        guest_peer_id = host_ws.receive_json()["participant_id"]

        _impostor_ws, impostor_existing = join_room(connect, host_peer_id, room_id)
        assert set(impostor_existing["participants"]) == {host_peer_id, guest_peer_id}
        impostor_peer_id = host_ws.receive_json()["participant_id"]
        guest_ws.receive_json()
        assert impostor_peer_id != host_peer_id

        guest_ws.send_json({"type": SignalType.OFFER.value, "target": host_peer_id, "sdp": "for_host"})

        relayed = host_ws.receive_json()
        assert (relayed["sdp"], relayed["from"]) == ("for_host", guest_peer_id)

    def test_relay_to_an_unknown_peer_leaves_the_sender_connected(self, connect):
        ws, _room_id = open_room(connect, "host1")

        ws.send_json({"type": SignalType.OFFER.value, "target": "nobody", "sdp": "lost"})
        ws.send_json({"type": SignalType.OFFER.value, "target": []})

        response = ws.receive_json()
        assert response["reason"] == ErrorReason.INVALID_MESSAGE.value


ABNORMAL_CLOSE_CODE = 4999


def resume_frame(seat, **overrides):
    return {
        "type": SignalType.RESUME.value,
        "participant_id": seat["participant_id"],
        "token": seat["resume_token"],
        "last_seq": seat["seq"],
        "sent_floor": 0,
        **overrides,
    }


@pytest.mark.integration
class TestResume:
    def test_guest_whose_socket_dropped_resumes_on_a_new_socket_and_the_host_hears_nothing(self, connect):
        host_ws, room_id = open_room(connect, "host1")
        guest_ws, seat = join_room(connect, "guest", room_id)
        host_ws.receive_json()
        guest_ws.close(code=ABNORMAL_CLOSE_CODE)

        resumed_ws = connect("guest")
        resumed_ws.send_json(resume_frame(seat))
        resumed = resumed_ws.receive_json()
        resumed_ws.send_json({"type": SignalType.CHAT.value, "message": "back"})

        assert resumed["type"] == SignalType.RESUMED.value
        assert resumed["resume_token"] != seat["resume_token"]
        assert host_ws.receive_json()["message"] == "back"

    def test_chat_sent_while_the_guest_was_away_is_replayed_after_resumed(self, connect):
        host_ws, room_id = open_room(connect, "host1")
        guest_ws, seat = join_room(connect, "guest", room_id)
        host_ws.receive_json()
        guest_ws.close(code=ABNORMAL_CLOSE_CODE)
        host_ws.send_json({"type": SignalType.CHAT.value, "message": "while you were away"})

        resumed_ws = connect("guest")
        resumed_ws.send_json(resume_frame(seat))

        assert resumed_ws.receive_json()["type"] == SignalType.RESUMED.value
        assert resumed_ws.receive_json()["message"] == "while you were away"

    def test_refused_resume_leaves_the_socket_free_to_join_afresh(self, connect):
        host_ws, room_id = open_room(connect, "host1")
        guest_ws, seat = join_room(connect, "guest", room_id)
        host_ws.receive_json()
        guest_ws.close(code=ABNORMAL_CLOSE_CODE)

        retry_ws = connect("guest")
        retry_ws.send_json(resume_frame(seat, **{"token": "not-the-token"}))
        refusal = retry_ws.receive_json()
        retry_ws.send_json({"type": SignalType.JOIN.value, "room_id": room_id})

        assert refusal["reason"] == ErrorReason.RESUME_REFUSED.value
        assert retry_ws.receive_json()["type"] == SignalType.EXISTING_PARTICIPANTS.value

    def test_resume_while_the_old_socket_is_still_open_closes_the_old_one(self, connect):
        host_ws, room_id = open_room(connect, "host1")
        guest_ws, seat = join_room(connect, "guest", room_id)
        host_ws.receive_json()

        resumed_ws = connect("guest")
        resumed_ws.send_json(resume_frame(seat))

        assert resumed_ws.receive_json()["type"] == SignalType.RESUMED.value
        with pytest.raises(WebSocketDisconnect) as superseded:
            guest_ws.receive_json()
        assert superseded.value.code == SEAT_CLOSED_BY_SERVER_CODE


def open_socket_as(client, member_named, account):
    return client.websocket_connect(WEBSOCKET_PATH, headers=handshake_headers(member_named(account)))


@pytest.fixture
def idle_sockets(client, member_named):
    """Opens the account's idle sockets, in a count the test names, and closes them after the test."""
    with ExitStack() as sessions:
        yield lambda account, count: [
            sessions.enter_context(open_socket_as(client, member_named, account)) for _ in range(count)
        ]


def assert_refused_for_too_many_calls(websocket):
    refusal = websocket.receive_json()
    with pytest.raises(WebSocketDisconnect) as closed:
        websocket.receive_json()
    assert (refusal, closed.value.code) == (
        {
            "type": SignalType.ERROR.value,
            "reason": ErrorReason.TOO_MANY_CALLS.value,
            "message": refusal["message"],
            "limit": MAX_LIVE_SEATS_PER_ACCOUNT,
        },
        POLICY_VIOLATION_CODE,
    )


def assert_admitted(websocket):
    websocket.send_json({"type": SignalType.JOIN.value})
    assert websocket.receive_json()["type"] == SignalType.ROOM_CREATED.value


@pytest.mark.integration
class TestCallCap:
    def test_socket_beyond_the_cap_gets_the_refusal_frame_and_a_policy_close(self, client, member_named, idle_sockets):
        idle_sockets("busy", MAX_LIVE_SEATS_PER_ACCOUNT)

        with open_socket_as(client, member_named, "busy") as ninth:
            assert_refused_for_too_many_calls(ninth)

    def test_other_account_is_admitted_while_one_account_is_at_the_cap(self, client, member_named, idle_sockets):
        idle_sockets("busy", MAX_LIVE_SEATS_PER_ACCOUNT)

        with open_socket_as(client, member_named, "other") as other:
            assert_admitted(other)

    def test_refused_socket_opens_no_seat_so_closing_a_live_one_lets_a_new_socket_in(
        self, client, member_named, idle_sockets
    ):
        live = idle_sockets("busy", MAX_LIVE_SEATS_PER_ACCOUNT)
        with open_socket_as(client, member_named, "busy") as ninth:
            assert_refused_for_too_many_calls(ninth)
        live[0].close()

        with open_socket_as(client, member_named, "busy") as admitted:
            assert_admitted(admitted)

    def test_parked_seat_does_not_count_toward_the_cap(self, client, connect, member_named, idle_sockets):
        host_ws, room_id = open_room(connect, "host1")
        guest_ws, _ = join_room(connect, "busy", room_id)
        host_ws.receive_json()
        guest_ws.close(code=ABNORMAL_CLOSE_CODE)
        idle_sockets("busy", MAX_LIVE_SEATS_PER_ACCOUNT - 1)

        with open_socket_as(client, member_named, "busy") as admitted:
            assert_admitted(admitted)

    def test_resume_is_admitted_when_the_old_socket_is_the_cap_overlap(self, connect, idle_sockets):
        host_ws, room_id = open_room(connect, "host1")
        _, seat = join_room(connect, "busy", room_id)
        host_ws.receive_json()
        idle_sockets("busy", MAX_LIVE_SEATS_PER_ACCOUNT - 2)
        resumed_ws = connect("busy")

        resumed_ws.send_json(resume_frame(seat))

        assert resumed_ws.receive_json()["type"] == SignalType.RESUMED.value

    def test_resume_at_the_cap_ends_with_the_refusal(self, connect, idle_sockets):
        host_ws, room_id = open_room(connect, "host1")
        join_room(connect, "busy", room_id)
        host_ws.receive_json()
        idle_sockets("busy", MAX_LIVE_SEATS_PER_ACCOUNT - 1)

        assert_refused_for_too_many_calls(connect("busy"))

    def test_refusal_log_carries_the_cap_only(self, client, member_named, logs, idle_sockets):
        idle_sockets("busy", MAX_LIVE_SEATS_PER_ACCOUNT)
        with open_socket_as(client, member_named, "busy") as ninth:
            assert_refused_for_too_many_calls(ninth)

        member = member_named("busy")
        secrets_in_logs = [member.account.username, member.account.id, member.session.id]
        assert [set(entry) for entry in events_named(logs, "too_many_calls")] == [{"event", "limit", "log_level"}]
        assert [secret for secret in secrets_in_logs if secret in str(logs)] == []
