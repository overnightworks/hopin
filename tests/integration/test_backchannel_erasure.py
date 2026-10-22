import io
import json
import zipfile
from contextlib import ExitStack
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from webauth.config import installed_web_auth_config

from hopin.app import create_app
from hopin.auth.stores import SignInStores
from hopin.config.constants import (
    ACCOUNT_PATH,
    BACKCHANNEL_LOGOUT_PATH,
    CALL_PATH,
    HOME_PATH,
    HOST_GRACE_SECONDS,
    PERSON_EXPORT_PATH,
    POLICY_VIOLATION_CLOSE_CODE,
    RESUME_WINDOW_SECONDS,
    SESSION_ENDED_CLOSE_CODE,
    SESSION_RECHECK_SECONDS,
    WEBSOCKET_PATH,
    RoomClosedReason,
    SignalType,
)
from tests.fake_issuer import FakeIssuer
from tests.sign_in_settings import TEST_PUBLIC_ORIGIN
from tests.signed_in import cookie_header, handshake_headers, seed_member
from tests.unit.conftest import ManualScheduler

ABNORMAL_CLOSE_CODE = 4999
FOUND_ELSEWHERE = 302
UNAUTHENTICATED = 401
NOT_FOUND = 404

pytestmark = pytest.mark.integration


@pytest.fixture
def stores():
    return SignInStores.in_memory()


@pytest.fixture
def scheduler():
    return ManualScheduler()


@pytest.fixture
def issuer(sign_in_settings):
    return FakeIssuer(sign_in_settings())


@pytest.fixture
def app(sign_in_settings, issuer, stores, scheduler):
    return create_app(
        sign_in_settings().settings, issuer_transports=issuer.transports, stores=stores, scheduler=scheduler
    )


@pytest.fixture
def client(app):
    with TestClient(app, base_url=TEST_PUBLIC_ORIGIN, follow_redirects=False) as shared_portal_client:
        yield shared_portal_client


@pytest.fixture
def members(app, stores):
    """The seeded member of a label; the label is the subject, so the same label is the same person."""
    config = installed_web_auth_config(app)
    seeded = {}

    def member_named(label, *, role=None):
        if label not in seeded:
            account = stores.accounts.create_for_subject(label, label, config.user_role if role is None else role)
            seeded[label] = seed_member(stores, config, account=account)
        return seeded[label]

    return member_named


@pytest.fixture
def sockets(client, members):
    """Opens a socket for a person; the first frame of every socket is its join, the next ones are numbered."""
    with ExitStack() as open_sockets:

        def connect(label):
            return open_sockets.enter_context(
                client.websocket_connect(WEBSOCKET_PATH, headers=handshake_headers(members(label)))
            )

        yield connect


@pytest.fixture
def advance(client, scheduler):
    return lambda seconds: client.portal.call(scheduler.advance, seconds)


@pytest.fixture
def erase(client, issuer):
    def post_logout_token(subject):
        return client.post(
            BACKCHANNEL_LOGOUT_PATH,
            content=urlencode({"logout_token": issuer.logout_token(subject)}),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )

    return post_logout_token


def host_a_call(sockets, label):
    host = sockets(label)
    host.send_json({"type": SignalType.JOIN.value})
    return host, host.receive_json()["room_id"]


def join_the_call(sockets, label, room_id):
    guest = sockets(label)
    guest.send_json({"type": SignalType.JOIN.value, "room_id": room_id})
    guest.receive_json()
    return guest


def a_call_with_a_guest(sockets, host_label, guest_label):
    host, room_id = host_a_call(sockets, host_label)
    guest = join_the_call(sockets, guest_label, room_id)
    assert host.receive_json()["type"] == SignalType.PARTICIPANT_JOINED.value
    return host, guest, room_id


def chat(ws, message):
    ws.send_json({"type": SignalType.CHAT.value, "message": message, "seq": 1})


def close_code_of(ws):
    with pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_json()
    return closed.value.code


def next_chat_message(ws):
    while (frame := ws.receive_json())["type"] != SignalType.CHAT.value:
        pass
    return frame["message"]


class TestSeatsOfAnErasedPerson:
    def test_a_guest_who_chats_after_the_erasure_is_closed_and_the_host_hears_the_leave_not_the_chat(
        self, sockets, erase
    ):
        host, guest, _ = a_call_with_a_guest(sockets, "ben", "anna")
        assert erase("anna").is_success

        chat(guest, "after the erasure")

        assert close_code_of(guest) == SESSION_ENDED_CLOSE_CODE
        assert host.receive_json()["type"] == SignalType.PARTICIPANT_LEFT.value
        host.send_json({"type": SignalType.LOCK_ROOM.value, "seq": 1})
        assert host.receive_json()["type"] == SignalType.ROOM_LOCKED.value

    def test_a_silent_guest_is_closed_at_the_next_minute_tick_and_the_host_hears_the_leave(
        self, stores, sockets, erase, advance
    ):
        host, guest, _ = a_call_with_a_guest(sockets, "ben", "anna")
        erase("anna")

        advance(SESSION_RECHECK_SECONDS)

        assert stores.accounts.get("anna") is None
        assert close_code_of(guest) == SESSION_ENDED_CLOSE_CODE
        assert host.receive_json()["type"] == SignalType.PARTICIPANT_LEFT.value

    def test_a_call_the_erased_person_hosts_ends_for_everyone_after_the_grace(
        self, client, members, sockets, erase, advance
    ):
        host, guest, room_id = a_call_with_a_guest(sockets, "anna", "ben")
        erase("anna")

        advance(SESSION_RECHECK_SECONDS)

        assert close_code_of(host) == SESSION_ENDED_CLOSE_CODE
        assert guest.receive_json()["type"] == SignalType.HOST_AWAY.value
        assert guest.receive_json()["type"] == SignalType.PARTICIPANT_LEFT.value
        advance(HOST_GRACE_SECONDS)
        closed = guest.receive_json()
        assert closed["type"] == SignalType.ROOM_CLOSED.value
        assert closed["reason"] == RoomClosedReason.HOST_GONE.value
        ben_cookies = {"cookie": cookie_header(members("ben").set_cookie_headers)}
        assert client.get(f"{CALL_PATH}/{room_id}", headers=ben_cookies).status_code == NOT_FOUND

    def test_a_parked_seat_of_the_erased_person_cannot_be_resumed_and_expires_with_the_park(
        self, client, members, sockets, erase, advance
    ):
        host, guest, _ = a_call_with_a_guest(sockets, "ben", "anna")
        guest.close(code=ABNORMAL_CLOSE_CODE)
        erase("anna")

        with (
            pytest.raises(WebSocketDisconnect) as refused,
            client.websocket_connect(WEBSOCKET_PATH, headers=handshake_headers(members("anna"))),
        ):
            pass
        advance(RESUME_WINDOW_SECONDS)

        assert refused.value.code == POLICY_VIOLATION_CLOSE_CODE
        assert host.receive_json()["type"] == SignalType.PARTICIPANT_LEFT.value

    def test_everyone_else_in_the_call_keeps_their_account_sessions_and_chat(self, stores, members, sockets, erase):
        host, guest, room_id = a_call_with_a_guest(sockets, "ben", "anna")
        third = join_the_call(sockets, "carol", room_id)
        host.receive_json()
        guest.receive_json()
        erase("anna")
        chat(guest, "after the erasure")
        assert close_code_of(guest) == SESSION_ENDED_CLOSE_CODE
        host.receive_json()

        chat(host, "for carol")

        assert next_chat_message(third) == "for carol"
        assert stores.accounts.get("ben") == members("ben").account
        assert stores.sessions.load(members("ben").session.id) is not None

    def test_both_seats_of_a_person_in_two_calls_are_closed(self, sockets, erase, advance):
        _, first_call = host_a_call(sockets, "ben")
        _, second_call = host_a_call(sockets, "carol")
        in_first = join_the_call(sockets, "anna", first_call)
        in_second = join_the_call(sockets, "anna", second_call)
        erase("anna")

        advance(SESSION_RECHECK_SECONDS)

        assert close_code_of(in_first) == SESSION_ENDED_CLOSE_CODE
        assert close_code_of(in_second) == SESSION_ENDED_CLOSE_CODE


class TestRequestsOfAnErasedPerson:
    def test_a_navigation_is_sent_to_the_sign_in_and_an_api_call_is_refused_never_a_server_error(
        self, client, members, erase
    ):
        cookies = {"cookie": cookie_header(members("anna").set_cookie_headers)}
        assert client.get(ACCOUNT_PATH, headers=cookies).status_code == 200
        erase("anna")

        navigation = client.get(HOME_PATH, headers={**cookies, "accept": "text/html"})
        api_call = client.get(ACCOUNT_PATH, headers=cookies)

        assert navigation.status_code == FOUND_ELSEWHERE
        assert api_call.status_code == UNAUTHENTICATED


def export_of(client, admin, subject):
    answer = client.get(
        PERSON_EXPORT_PATH.format(subject=subject), headers={"cookie": cookie_header(admin.set_cookie_headers)}
    )
    assert answer.status_code == 200
    with zipfile.ZipFile(io.BytesIO(answer.content)) as archive:
        export = json.loads(archive.read("export.json"))
    return export["data"], {entry["kind"]: entry["count"] for entry in export["withheld"]}


class TestExportAfterTheErasure:
    def test_the_export_shows_the_seat_until_the_tick_and_nothing_after_it(
        self, client, app, members, sockets, erase, advance
    ):
        admin = members("olga", role=installed_web_auth_config(app).admin_role)
        _, guest, _ = a_call_with_a_guest(sockets, "ben", "anna")
        erase("anna")

        data_before, withheld_before = export_of(client, admin, "anna")
        advance(SESSION_RECHECK_SECONDS)
        assert close_code_of(guest) == SESSION_ENDED_CLOSE_CODE
        data_after, withheld_after = export_of(client, admin, "anna")

        assert data_before["account"] is None
        assert data_before["sessions"] == []
        assert len(data_before["seats"]) == 1
        assert withheld_before["resume_token"] == 1
        assert data_after == {"account": None, "sessions": [], "seats": []}
        assert set(withheld_after.values()) == {0}
