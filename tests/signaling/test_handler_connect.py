from datetime import timedelta

import pytest
from starlette.websockets import WebSocketDisconnect

from hopin.config.constants import (
    WEBSOCKET_PATH,
    ErrorReason,
    SignalType,
)
from tests.signaling.support.flow import POLICY_VIOLATION_CODE, events_named, expires_at
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN
from tests.support.signed_in import cookie_header, handshake_headers, parsed_set_cookies


def refused_with_code(client, headers, path=WEBSOCKET_PATH):
    with pytest.raises(WebSocketDisconnect) as refused, client.websocket_connect(path, headers=headers):
        pass
    return refused.value.code


def joined_room(client, member):
    with client.websocket_connect(WEBSOCKET_PATH, headers=handshake_headers(member)) as ws:
        ws.send_json({"type": SignalType.JOIN.value})
        return ws.receive_json()


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

    def test_logs_of_a_refused_recheck_carry_no_account_username_cookie_or_session_id(
        self, client, served_sessions, stores, seed, logs
    ):
        member = seed()
        with client.websocket_connect(WEBSOCKET_PATH, headers=handshake_headers(member)) as ws:
            ws.send_json({"type": SignalType.JOIN.value})
            ws.receive_json()
            stores.sessions.delete(member.session.id)
            ws.send_json({"type": SignalType.CHAT.value, "seq": 1, "message": "hello"})
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
            served_sessions.wait_for_the_session_to_end()

        cookie_values = [morsel.value for _, morsel in parsed_set_cookies(member.set_cookie_headers)]
        secrets_in_logs = [member.account.username, member.account.id, member.session.id]
        assert [secret for secret in [*secrets_in_logs, *cookie_values] if secret in str(logs)] == []
        assert [set(entry) for entry in events_named(logs, "handshake_refused")] == [{"event", "reason", "log_level"}]
        assert [set(entry) for entry in events_named(logs, "session_ended")] == [{"event", "peer_id", "log_level"}]
