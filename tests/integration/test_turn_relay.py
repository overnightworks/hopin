import json
import secrets
import traceback
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
import structlog.testing
from fastapi.testclient import TestClient
from webauth.config import installed_web_auth_config

from hopin.app import create_app
from hopin.auth.stores import SignInStores
from hopin.config.constants import ICE_SERVERS_PATH
from hopin.config.secret_files import StartRefusedError
from hopin.web.ice_servers import TurnRelayFailure, TurnRelayUnavailableError
from hopin.web.turn_relay import TurnAnswer, build_turn_relay
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN
from tests.support.signed_in import cookie_header, seed_member

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    import urllib3

pytestmark = pytest.mark.integration

ENDPOINT = "https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}/credentials/generate-ice-servers"
PADDED_RFC_TOKEN = "aZ09._~+/-=="  # noqa: S105
FETCH_ACCEPT = {"accept": "application/json"}
STUN_ENTRY = {"urls": ["stun:stun.cloudflare.com:3478"]}
RELAY_URLS = [
    "turn:turn.cloudflare.com:3478?transport=udp",
    "turn:turn.cloudflare.com:3478?transport=tcp",
    "turns:turn.cloudflare.com:5349?transport=tcp",
    "turns:turn.cloudflare.com:443?transport=tcp",
]
UNREADABLE_FILE_STATES = {
    "missing": None,
    "unreadable": "directory",
    "empty": b"",
    "only a newline": b"\n",
    "not text": b"\xff\xfe",
}


def provider_answer(urls=RELAY_URLS, *, username="provider-user", credential="provider-credential"):
    return {"iceServers": [STUN_ENTRY, {"urls": urls, "username": username, "credential": credential}]}


@dataclass
class FakeTransport:
    """The provider's side of the seam: records each call and answers the scenario."""

    status: int = 201
    body: bytes | Callable[[], bytes] = field(
        default_factory=lambda: json.dumps(provider_answer()).encode(), repr=False
    )
    calls: list[tuple[str, Mapping[str, str], bytes]] = field(default_factory=list, repr=False)

    def __call__(self, url: str, *, headers: Mapping[str, str], body: bytes, timeout: urllib3.Timeout) -> TurnAnswer:
        self.calls.append((url, headers, body))
        return TurnAnswer(self.status, self.body() if callable(self.body) else self.body)


@dataclass
class TurnKeys:
    key_id: str = field(default_factory=lambda: secrets.token_hex(16))
    api_token: str = field(default_factory=lambda: secrets.token_urlsafe(48), repr=False)


@pytest.fixture
def turn_settings(sign_in_settings, tmp_path):
    def build(keys: TurnKeys | None = None, **overrides):
        keys = keys or TurnKeys()
        (tmp_path / "turn-key-id").write_text(keys.key_id + "\n")
        (tmp_path / "turn-api-token").write_text(keys.api_token + "\n")
        return sign_in_settings(
            turn_provider="cloudflare",
            turn_key_id_file=tmp_path / "turn-key-id",
            turn_api_token_file=tmp_path / "turn-api-token",
            **overrides,
        ).settings

    return build


def member_client(settings, transport, *, signed_in: bool = True) -> TestClient:
    stores = SignInStores.in_memory()
    app = create_app(settings, stores=stores, turn_transport=transport)
    headers = dict(FETCH_ACCEPT)
    if signed_in:
        headers["cookie"] = cookie_header(seed_member(stores, installed_web_auth_config(app)).set_cookie_headers)
    return TestClient(app, base_url=TEST_PUBLIC_ORIGIN, headers=headers, follow_redirects=False)


def served_by(settings, transport, *, signed_in: bool = True):
    return member_client(settings, transport, signed_in=signed_in).get(ICE_SERVERS_PATH)


def relay_entry(response):
    return response.json()["iceServers"][1:]


def write_file_state(path: Path, state) -> None:
    path.unlink()
    if state == "directory":
        path.mkdir()
    elif state is not None:
        path.write_bytes(state)


def refusal_of(settings) -> StartRefusedError:
    transport = FakeTransport()
    with pytest.raises(StartRefusedError) as refusal:
        create_app(settings, turn_transport=transport)
    return refusal.value


def assert_refusal_leaks_nothing(error, *, hidden, capsys) -> None:
    rendered = "".join(traceback.format_exception(error))
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    assert all(value not in rendered for value in hidden)
    assert "Errno" not in rendered
    assert capsys.readouterr() == ("", "")


class TestStart:
    @pytest.mark.parametrize("state", UNREADABLE_FILE_STATES)
    @pytest.mark.parametrize("setting", ["turn_key_id_file", "turn_api_token_file"])
    def test_a_key_file_that_cannot_be_used_stops_the_start_without_leaking(
        self, turn_settings, capsys, setting, state
    ):
        settings = turn_settings()
        path = getattr(settings, setting)
        sentinel = secrets.token_urlsafe(16)
        write_file_state(path, UNREADABLE_FILE_STATES[state] if state != "not text" else sentinel.encode() + b"\xff")

        error = refusal_of(settings)

        assert setting in str(error)
        assert_refusal_leaks_nothing(error, hidden=[sentinel, str(path)], capsys=capsys)

    @pytest.mark.parametrize("key_id", ["a/b", "a?b", "a#b", "a@b", "..", "a b", "a%2Fb", "a\nb"])
    def test_a_key_id_that_is_unsafe_in_a_url_path_stops_the_start_without_echoing_it(
        self, turn_settings, capsys, key_id
    ):
        keys = TurnKeys(key_id=key_id)

        error = refusal_of(turn_settings(keys))

        assert "turn_key_id_file" in str(error)
        assert_refusal_leaks_nothing(error, hidden=[key_id], capsys=capsys)

    @pytest.mark.parametrize("token", ["a\rb", "a\nb", "a\r\n b", "a b", "a\x00b", 'a"b', "a,b", "café", "Ā"])
    def test_a_token_that_is_unsafe_in_a_header_stops_the_start_naming_only_its_setting(
        self, turn_settings, capsys, token
    ):
        error = refusal_of(turn_settings(TurnKeys(api_token=token)))

        assert "turn_api_token_file" in str(error)
        assert "turn_key_id_file" not in str(error)
        assert_refusal_leaks_nothing(error, hidden=[token, "UnicodeEncodeError", "ValueError"], capsys=capsys)

    def test_a_token_with_padding_and_the_rfc_6750_symbols_is_accepted(self, turn_settings):
        assert build_turn_relay(turn_settings(TurnKeys(api_token=PADDED_RFC_TOKEN)), FakeTransport()) is not None

    def test_turn_off_builds_no_relay_and_reads_no_file(self, sign_in_settings, tmp_path):
        settings = sign_in_settings(turn_key_id_file=tmp_path / "gone", turn_api_token_file=tmp_path / "gone").settings
        assert build_turn_relay(settings, FakeTransport()) is None


class TestCredentials:
    def test_a_call_start_posts_one_request_with_the_key_the_token_and_a_12_hour_ttl(self, turn_settings):
        keys = TurnKeys()
        transport = FakeTransport()

        served_by(turn_settings(keys), transport)

        [(url, headers, body)] = transport.calls
        assert url == ENDPOINT.format(key_id=keys.key_id)
        assert headers["Authorization"] == f"Bearer {keys.api_token}"
        assert headers["Content-Type"] == "application/json"
        assert body == b'{"ttl": 43200}'

    def test_each_call_start_asks_again_and_serves_its_own_credential(self, turn_settings):
        answers = iter(secrets.token_urlsafe(8) for _ in range(2))
        transport = FakeTransport(body=lambda: json.dumps(provider_answer(credential=next(answers))).encode())
        settings = turn_settings()

        first = served_by(settings, transport)
        second = served_by(settings, transport)

        assert len(transport.calls) == 2
        assert relay_entry(first)[0]["credential"] != relay_entry(second)[0]["credential"]

    def test_the_answer_keeps_the_stun_setting_and_the_relay_entry_without_the_providers_stun(self, turn_settings):
        settings = turn_settings(stun_server="stun:stun.example.test:3478")
        urls_with_port_53 = [
            *RELAY_URLS,
            "turn:turn.cloudflare.com:53?transport=udp",
            "turn:turn.cloudflare.com:53?transport=tcp",
        ]
        transport = FakeTransport(body=json.dumps(provider_answer(urls_with_port_53)).encode())

        response = served_by(settings, transport)

        assert response.json() == {
            "iceServers": [
                {"urls": ["stun:stun.example.test:3478"]},
                {"urls": RELAY_URLS, "username": "provider-user", "credential": "provider-credential"},
            ]
        }

    def test_only_urls_on_port_53_are_dropped(self, turn_settings):
        urls = [
            "turn:h:53?transport=udp",
            "turns:h:53?transport=tcp",
            "turns:h:5349?transport=tcp",
            "turn:h:3478?transport=udp",
            "turns:h:443?transport=tcp",
            "turn:h:530?transport=udp",
            "turn:h:5353",
            "turn:h?transport=udp",
        ]
        transport = FakeTransport(body=json.dumps(provider_answer(urls)).encode())

        response = served_by(turn_settings(), transport)

        assert relay_entry(response)[0]["urls"] == urls[2:]

    def test_a_signed_out_fetch_is_refused_and_asks_the_provider_for_nothing(self, turn_settings):
        transport = FakeTransport()

        response = served_by(turn_settings(), transport, signed_in=False)

        assert (response.status_code, transport.calls) == (401, [])


MALFORMED_BODIES = {
    "not json": b"{",
    "a json array": b"[]",
    "iceServers missing": b"{}",
    "iceServers not a list": b'{"iceServers": "x"}',
    "iceServers empty": b'{"iceServers": []}',
    "a TURN entry without a username": json.dumps({"iceServers": [{"urls": RELAY_URLS, "credential": "c"}]}).encode(),
    "a TURN entry without a credential": json.dumps({"iceServers": [{"urls": RELAY_URLS, "username": "u"}]}).encode(),
    "a non-string username": json.dumps(
        {"iceServers": [{"urls": RELAY_URLS, "username": 7, "credential": "c"}]}
    ).encode(),
    "a non-string credential": json.dumps(
        {"iceServers": [{"urls": RELAY_URLS, "username": "u", "credential": 7}]}
    ).encode(),
    "urls a string": json.dumps({"iceServers": [{"urls": RELAY_URLS[0], "username": "u", "credential": "c"}]}).encode(),
    "an http url in a credentialed entry": json.dumps(provider_answer(["http://h:3478"])).encode(),
    "an entry mixing stun and turn": json.dumps(provider_answer(["stun:h:3478", RELAY_URLS[0]])).encode(),
    "only port 53 urls": json.dumps(provider_answer(["turn:h:53?transport=udp", "turns:h:53?transport=tcp"])).encode(),
    "stun only": json.dumps({"iceServers": [STUN_ENTRY]}).encode(),
}


class TestProviderFailures:
    @pytest.mark.parametrize("body", MALFORMED_BODIES.values(), ids=MALFORMED_BODIES.keys())
    def test_a_malformed_answer_gives_stun_only_and_a_malformed_warning(self, turn_settings, body):
        settings = turn_settings()
        client = member_client(settings, FakeTransport(body=body))
        with structlog.testing.capture_logs() as records:
            response = client.get(ICE_SERVERS_PATH)

        assert (response.status_code, response.json()["iceServers"]) == (200, [{"urls": [settings.stun_server]}])
        assert records == [{"event": "turn_relay_unavailable", "log_level": "warning", "reason": "malformed"}]

    @pytest.mark.parametrize("status", [200, 201])
    def test_any_2xx_answer_is_served(self, turn_settings, status):
        assert len(relay_entry(served_by(turn_settings(), FakeTransport(status=status)))) == 1

    @pytest.mark.parametrize("status", [302, 401, 403, 404, 429, 500])
    def test_any_other_status_gives_stun_only_and_a_refused_warning_with_the_status(self, turn_settings, status):
        client = member_client(turn_settings(), FakeTransport(status=status))
        with structlog.testing.capture_logs() as records:
            response = client.get(ICE_SERVERS_PATH)

        assert relay_entry(response) == []
        assert records == [
            {"event": "turn_relay_unavailable", "log_level": "warning", "reason": "refused", "status": status}
        ]

    @pytest.mark.parametrize("reason", [TurnRelayFailure.UNREACHABLE, TurnRelayFailure.TIMED_OUT])
    def test_a_transport_that_cannot_reach_the_provider_gives_stun_only_and_its_reason(self, turn_settings, reason):
        def unreachable(url, *, headers, body, timeout):
            raise TurnRelayUnavailableError(reason)

        client = member_client(turn_settings(), unreachable)
        with structlog.testing.capture_logs() as records:
            response = client.get(ICE_SERVERS_PATH)

        assert relay_entry(response) == []
        assert [record["reason"] for record in records] == [reason]


class TestSecrets:
    def test_no_repr_shows_a_key_id_a_token_or_a_credential(self, turn_settings):
        keys = TurnKeys()
        settings = turn_settings(keys)
        relay = build_turn_relay(settings, FakeTransport())
        served = relay.relay_servers()

        rendered = repr(settings) + repr(relay) + repr(served)

        assert all(value not in rendered for value in [keys.key_id, keys.api_token, "provider-credential"])

    def test_no_log_line_shows_a_key_id_a_token_a_username_or_a_credential(self, turn_settings, capsys):
        keys = TurnKeys()
        settings = turn_settings(keys)
        clients = [
            member_client(settings, transport)
            for transport in [FakeTransport(), FakeTransport(status=403), FakeTransport(body=b"{")]
        ]
        with structlog.testing.capture_logs() as records:
            for client in clients:
                client.get(ICE_SERVERS_PATH)

        shown = repr(records) + "".join(capsys.readouterr())
        assert all(
            value not in shown for value in [keys.key_id, keys.api_token, "provider-user", "provider-credential"]
        )
