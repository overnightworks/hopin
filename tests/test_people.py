import io
import json
import secrets
import zipfile
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
import structlog.testing
from fastapi.testclient import TestClient
from pydantic import SecretStr
from webauth.config import install_web_auth_config, installed_web_auth_config

from hopin.app import create_app
from hopin.auth.stores import SignInStores
from hopin.config.constants import (
    ADMIN_GROUP,
    AUTH_CALLBACK_PATH,
    AUTH_START_PATH,
    HEALTH_PATH,
    HOME_PATH,
    LOGIN_GROUP,
    NO_ACCESS_PATH,
    RETURN_TO_QUERY_PARAMETER,
    WEBSOCKET_PATH,
    SignalType,
)
from tests.support.fake_issuer import FakeIssuer
from tests.support.people_export import ask, export_path, read_archive, withheld_counts
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN
from tests.support.signed_in import handshake_headers, new_id_token, seed_member

pytestmark = pytest.mark.integration

WITHHELD_KINDS = ["session_id", "refresh_token", "id_token", "resume_token", "call_replay_frame"]
ZERO_WITHHELD = [
    {"kind": kind, "count": 0, "reason": reason}
    for kind, reason in zip(WITHHELD_KINDS, ["secret", "secret", "secret", "secret", "others"], strict=True)
]


@pytest.fixture
def stores():
    return SignInStores.in_memory()


@pytest.fixture
def app(sign_in_settings, stores):
    return create_app(sign_in_settings().settings, stores=stores)


@pytest.fixture
def client(app):
    with TestClient(app, base_url=TEST_PUBLIC_ORIGIN, follow_redirects=False) as shared_portal_client:
        yield shared_portal_client


@pytest.fixture
def config(app):
    return installed_web_auth_config(app)


@pytest.fixture
def person(stores, config):
    """A signed-in person whose subject is the label; a role other than a member's makes them an admin."""

    def make(subject, role=None):
        account = stores.accounts.create_for_subject(subject, subject, config.user_role if role is None else role)
        return seed_member(stores, config, account=account)

    return make


@pytest.fixture
def admin(person, config):
    return person("olga", role=config.admin_role)


def stored_state_of_anna(stores, scene):
    """Everything stored about anna: her account, her collection and each complete session, secrets revealed."""
    sessions = []
    for session_id in scene.session_ids:
        stored = stores.sessions.load(session_id)
        refresh_token = None if stored.refresh_token is None else stored.refresh_token.get_secret_value()
        sessions.append((stored.id, stored.user, stored.created_at, stored.expires_at, refresh_token))
    return stores.accounts.get("anna"), stores.sessions.person_records("anna"), sessions


@dataclass
class Scene:
    """Anna hosts a call with ben in it and has two sessions, one of them with a refresh token."""

    room_id: str
    refresh_token: str
    session_ids: list[str]
    chat_text: str
    ben_chat_text: str
    session_times: list[tuple[datetime, datetime]]
    ben_subject: str
    anna_frames_held: int
    sensitive: list[str] = field(repr=False)


@pytest.fixture
def scene(client, stores, person):
    anna = person("anna")
    ben_subject = f"ben-{secrets.token_hex(4)}"
    ben = person(ben_subject)
    refresh_token = secrets.token_urlsafe()
    older_id_token = new_id_token()
    older = stores.sessions.create(
        "anna",
        now=anna.session.created_at - timedelta(hours=1),
        expires_at=anna.session.expires_at - timedelta(hours=2),
        refresh_token=SecretStr(refresh_token),
        id_token=older_id_token,
    )
    chat_text = secrets.token_urlsafe()
    ben_chat_text = secrets.token_urlsafe()
    with ExitStack() as open_sockets:
        anna_socket = open_sockets.enter_context(
            client.websocket_connect(WEBSOCKET_PATH, headers=handshake_headers(anna))
        )
        anna_socket.send_json({"type": SignalType.JOIN.value})
        created = anna_socket.receive_json()
        ben_socket = open_sockets.enter_context(
            client.websocket_connect(WEBSOCKET_PATH, headers=handshake_headers(ben))
        )
        ben_socket.send_json({"type": SignalType.JOIN.value, "room_id": created["room_id"]})
        ben_seat = ben_socket.receive_json()
        frames_to_anna = [created, anna_socket.receive_json()]
        anna_socket.send_json({"type": SignalType.CHAT.value, "message": chat_text, "seq": 1})
        ben_socket.receive_json()
        ben_socket.send_json({"type": SignalType.CHAT.value, "message": ben_chat_text, "seq": 1})
        frames_to_anna.append(anna_socket.receive_json())
        yield Scene(
            room_id=created["room_id"],
            refresh_token=refresh_token,
            session_ids=[anna.session.id, older.id],
            chat_text=chat_text,
            ben_chat_text=ben_chat_text,
            session_times=[
                (older.created_at, anna.session.expires_at - timedelta(hours=2)),
                (anna.session.created_at, anna.session.expires_at),
            ],
            ben_subject=ben_subject,
            anna_frames_held=len(frames_to_anna),
            sensitive=[
                anna.session.id,
                older.id,
                refresh_token,
                anna.session.id_token.get_secret_value(),
                older_id_token.get_secret_value(),
                created["resume_token"],
                created["participant_id"],
                ben_seat["resume_token"],
                ben_seat["participant_id"],
                chat_text,
                ben_chat_text,
                ben_subject,
            ],
        )


class TestExportOfAPerson:
    def test_answers_a_zip_of_export_json_listing_the_account_sessions_and_seats(self, client, admin, scene, config):
        answer = ask(client, "anna", admin)

        names, export = read_archive(answer.content)
        assert answer.status_code == 200
        assert answer.headers["content-type"] == "application/zip"
        assert answer.headers["content-disposition"] == 'attachment; filename="hopin-person-export.zip"'
        assert names == ["export.json"]
        assert list(export) == ["people_export", "app", "subject", "exported_at", "data", "files", "withheld"]
        assert (export["people_export"], export["app"], export["subject"], export["files"]) == (1, "hopin", "anna", [])
        assert export["data"]["account"] == {"name": "anna", "role": config.user_role}
        assert export["data"]["sessions"] == [
            {
                "started_at": started.isoformat().replace("+00:00", "Z"),
                "expires_at": expires.isoformat().replace("+00:00", "Z"),
            }
            for started, expires in scene.session_times
        ]
        assert export["data"]["seats"] == [{"call_id": scene.room_id, "host": True}]
        assert export["withheld"] == [
            {"kind": "session_id", "count": 2, "reason": "secret"},
            {"kind": "refresh_token", "count": 1, "reason": "secret"},
            {"kind": "id_token", "count": 2, "reason": "secret"},
            {"kind": "resume_token", "count": 1, "reason": "secret"},
            {"kind": "call_replay_frame", "count": scene.anna_frames_held, "reason": "others"},
        ]

    def test_counts_only_the_sessions_that_hold_an_id_token(self, client, stores, admin, person):
        anna = person("anna")
        stores.sessions.create(
            "anna", now=anna.session.created_at, expires_at=anna.session.expires_at, refresh_token=None, id_token=None
        )

        _, export = read_archive(ask(client, "anna", admin).content)

        withheld = {entry["kind"]: entry["count"] for entry in export["withheld"]}
        assert (withheld["session_id"], withheld["id_token"]) == (2, 1)

    def test_lists_the_sessions_oldest_first(self, client, admin, scene):
        _, export = read_archive(ask(client, "anna", admin).content)

        started = [entry["started_at"] for entry in export["data"]["sessions"]]
        assert len(set(started)) == 2
        assert started == sorted(started)

    def test_stamps_the_export_with_the_configured_clock_in_whole_seconds(self, app, client, admin, config):
        export_time = int(config.wall_clock()) + 90
        install_web_auth_config(app, replace(config, wall_clock=lambda: export_time + 0.75))

        _, export = read_archive(ask(client, "anna", admin).content)

        assert export["exported_at"] == datetime.fromtimestamp(export_time, UTC).isoformat().replace("+00:00", "Z")

    def test_holds_neither_a_secret_nor_another_persons_data(self, client, admin, scene):
        answer = ask(client, "anna", admin)

        with zipfile.ZipFile(io.BytesIO(answer.content)) as archive:
            text = archive.read("export.json").decode()
        leaked = [value for value in scene.sensitive if value in text or value.encode() in answer.content]
        assert leaked == []

    def test_holds_only_the_rows_and_seats_of_the_subject_asked_for(self, client, admin, scene):
        _, ben_export = read_archive(ask(client, scene.ben_subject, admin).content)
        _, other_case = read_archive(ask(client, "Anna", admin).content)

        assert ben_export["data"]["account"]["name"] == scene.ben_subject
        assert len(ben_export["data"]["sessions"]) == 1
        assert ben_export["data"]["seats"] == [{"call_id": scene.room_id, "host": False}]
        assert withheld_counts(ben_export)["session_id"] == 1
        assert other_case["subject"] == "Anna"
        assert other_case["data"] == {"account": None, "sessions": [], "seats": []}

    def test_lists_one_seat_for_each_socket_with_the_host_flag_of_its_room(self, client, admin, person):
        anna, ben = person("anna"), person("ben")
        with ExitStack() as open_sockets:
            sockets = [
                open_sockets.enter_context(client.websocket_connect(WEBSOCKET_PATH, headers=handshake_headers(member)))
                for member in (anna, anna, anna, ben)
            ]
            sockets[0].send_json({"type": SignalType.JOIN.value})
            sockets[0].receive_json()
            sockets[3].send_json({"type": SignalType.JOIN.value})
            guest_room = sockets[3].receive_json()["room_id"]
            sockets[1].send_json({"type": SignalType.JOIN.value, "room_id": guest_room})
            sockets[1].receive_json()

            _, export = read_archive(ask(client, "anna", admin).content)

        seats = export["data"]["seats"]
        hosted = [seat for seat in seats if seat["host"]]
        assert len(seats) == 3
        assert len(hosted) == 1
        assert hosted[0]["call_id"] not in {None, guest_room}
        assert {"call_id": guest_room, "host": False} in seats
        assert {"call_id": None, "host": False} in seats
        assert withheld_counts(export)["resume_token"] == 3

    def test_lists_the_account_of_a_person_with_no_session(self, client, admin, stores, config):
        stores.accounts.create_for_subject("anna", "anna", config.user_role)

        _, export = read_archive(ask(client, "anna", admin).content)

        assert export["data"] == {"account": {"name": "anna", "role": config.user_role}, "sessions": [], "seats": []}
        assert export["withheld"] == ZERO_WITHHELD

    def test_answers_an_empty_export_for_an_unknown_subject_and_creates_nothing(self, client, admin, stores):
        with structlog.testing.capture_logs() as logs:
            answer = ask(client, "ghost", admin)

        _, export = read_archive(answer.content)
        assert answer.status_code == 200
        assert export["subject"] == "ghost"
        assert export["data"] == {"account": None, "sessions": [], "seats": []}
        assert export["withheld"] == []
        assert export["files"] == []
        assert stores.accounts.get("ghost") is None
        assert "ghost" not in json.dumps(logs)

    def test_answers_the_head_of_the_export_without_a_body(self, client, admin, scene):
        got, head = ask(client, "anna", admin), ask(client, "anna", admin, method="HEAD")

        assert head.status_code == 200
        assert head.headers["content-type"] == got.headers["content-type"]
        assert head.headers["content-disposition"] == got.headers["content-disposition"]
        assert head.content == b""


class TestWhoMayExport:
    def test_refuses_a_member_who_is_not_an_admin_without_naming_the_person(self, client, person, scene):
        member = person("mallory")

        answer = ask(client, "anna", member)

        assert answer.status_code == 403
        assert "anna" not in answer.text
        assert ask(client, "anna", member, method="HEAD").status_code == 403

    def test_refuses_an_admin_who_became_a_member_at_her_next_sign_in(self, client, admin, stores, config):
        assert ask(client, "anna", admin).status_code == 200
        stores.accounts.set_role("olga", config.user_role)

        assert ask(client, "anna", admin).status_code == 403

    @pytest.mark.parametrize(
        ("accept", "status"), [("text/html", 302), ("application/json", 401)], ids=["navigation", "api call"]
    )
    def test_refuses_a_request_without_a_session_before_the_route(self, client, scene, accept, status):
        answer = ask(client, "anna", headers={"accept": accept})

        assert answer.status_code == status
        assert "anna" not in answer.text
        if status == 302:
            location = urlsplit(answer.headers["location"])
            assert location.path == AUTH_START_PATH
            assert parse_qs(location.query)[RETURN_TO_QUERY_PARAMETER] == [export_path("anna")]

    @pytest.mark.parametrize("method", ["POST", "DELETE"])
    @pytest.mark.parametrize("subject_path", [export_path("anna"), "/api/admin/people/anna"])
    def test_offers_no_way_to_change_a_person(self, client, admin, scene, stores, method, subject_path):
        before = stored_state_of_anna(stores, scene)

        answer = client.request(
            method,
            subject_path,
            headers={**handshake_headers(admin, origin=TEST_PUBLIC_ORIGIN)},
        )

        assert answer.status_code in {404, 405}
        assert stored_state_of_anna(stores, scene) == before
        assert len(before[2]) == 2


class TestCaching:
    @pytest.mark.parametrize(
        "request_of",
        [
            lambda client, admin, member: ask(client, "anna", admin),
            lambda client, admin, member: ask(client, "anna", member),
            lambda client, admin, member: ask(client, "anna", headers={"accept": "application/json"}),
            lambda client, admin, member: ask(client, "anna", headers={"accept": "text/html"}),
        ],
        ids=["200", "403", "401", "302"],
    )
    def test_every_answer_of_the_path_is_no_store(self, client, admin, person, request_of):
        assert request_of(client, admin, person("mallory")).headers["cache-control"] == "no-store"

    def test_other_pages_keep_their_cache_rules(self, client, admin):
        home = client.get(HOME_PATH, headers=handshake_headers(admin, origin=None))
        health = client.get(HEALTH_PATH)

        assert home.headers["cache-control"] == "no-cache"
        assert "no-store" not in health.headers.get("cache-control", "")


@pytest.fixture
def issuer_browser(sign_in_settings, stores):
    sign_in = sign_in_settings()
    issuer = FakeIssuer(sign_in)
    app = create_app(sign_in.settings, issuer_transports=issuer.transports, stores=stores)

    def sign_in_with(groups):
        browser = TestClient(app, base_url=TEST_PUBLIC_ORIGIN, follow_redirects=False)
        start = browser.get(AUTH_START_PATH)
        query = issuer.authorize(
            start.headers["location"], subject=secrets.token_hex(8), groups=groups, username="olga"
        )
        return browser, browser.get(f"{AUTH_CALLBACK_PATH}?{query}")

    return sign_in_with


class TestAdminGroup:
    @pytest.mark.parametrize("group", [ADMIN_GROUP, f"{ADMIN_GROUP}/team"])
    def test_an_admin_group_member_exports_after_signing_in(self, issuer_browser, group):
        browser, _ = issuer_browser([group])

        assert browser.get(export_path("anna")).status_code == 200

    @pytest.mark.parametrize("group", [LOGIN_GROUP, f"{LOGIN_GROUP}/administrators"])
    def test_a_member_outside_the_admin_group_is_refused(self, issuer_browser, group):
        browser, _ = issuer_browser([group])

        assert browser.get(export_path("anna")).status_code == 403

    def test_a_group_that_only_shares_the_login_groups_text_is_not_let_in(self, issuer_browser):
        _, callback = issuer_browser([f"{LOGIN_GROUP}x/admin"])

        assert callback.headers["location"] == NO_ACCESS_PATH
