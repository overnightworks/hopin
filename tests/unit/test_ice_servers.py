import re
import secrets

import pytest
import structlog.testing
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hopin.web.ice_servers import (
    IceServer,
    TurnRelay,
    TurnRelayFailure,
    TurnRelayUnavailableError,
    create_ice_servers_router,
)
from hopin.web.routes import STATIC_DIR

ICE_URL_PATTERN = re.compile(r"\b(stuns?|turns?):", re.IGNORECASE)


STUN_SERVER = "stun:stun.example.test:3478"


class RelayFake:
    def __init__(self, servers=None, failure=None):
        self.servers = servers or []
        self.failure = failure
        self.calls = 0

    def relay_servers(self):
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return self.servers


def client_for(stun_server: str, turn_relay: TurnRelay | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(create_ice_servers_router(stun_server, turn_relay))
    return TestClient(app)


@pytest.mark.parametrize("method", ["get", "head"])
def test_ice_servers_route_answers_ok_to_get_and_head(method):
    response = getattr(client_for("stun:stun.example.test:3478"), method)("/api/ice-servers")
    assert response.status_code == 200


def test_ice_servers_route_answers_in_the_browsers_rtc_configuration_shape():
    response = client_for("stun:stun.example.test:3478").get("/api/ice-servers")
    assert response.json() == {"iceServers": [{"urls": ["stun:stun.example.test:3478"]}]}


def test_a_relay_adds_its_entries_with_their_credentials_after_the_stun_entry():
    username, credential = secrets.token_urlsafe(8), secrets.token_urlsafe(8)
    relay = RelayFake([IceServer(urls=["turn:turn.example.test:3478"], username=username, credential=credential)])
    response = client_for(STUN_SERVER, relay).get("/api/ice-servers")
    assert response.json() == {
        "iceServers": [
            {"urls": [STUN_SERVER]},
            {"urls": ["turn:turn.example.test:3478"], "username": username, "credential": credential},
        ]
    }


def test_a_served_credential_is_not_shown_by_the_servers_repr():
    credential = secrets.token_urlsafe(8)
    assert credential not in repr(IceServer(urls=["turn:turn.example.test"], username="name", credential=credential))


def test_with_no_relay_the_route_answers_stun_only():
    response = client_for(STUN_SERVER, None).get("/api/ice-servers")
    assert response.json() == {"iceServers": [{"urls": [STUN_SERVER]}]}


@pytest.mark.parametrize("status", [None, 403])
@pytest.mark.parametrize("reason", list(TurnRelayFailure))
def test_a_relay_that_is_unavailable_gives_stun_only_and_one_warning_with_its_reason(reason, status):
    relay = RelayFake(failure=TurnRelayUnavailableError(reason, status))
    with structlog.testing.capture_logs() as records:
        response = client_for(STUN_SERVER, relay).get("/api/ice-servers")
    expected = {"event": "turn_relay_unavailable", "log_level": "warning", "reason": reason}
    assert (response.status_code, response.json()) == (200, {"iceServers": [{"urls": [STUN_SERVER]}]})
    assert records == [expected if status is None else {**expected, "status": status}]


def test_static_files_name_no_ice_server_url():
    offenders = [
        path.name
        for path in STATIC_DIR.rglob("*")
        if path.is_file() and ICE_URL_PATTERN.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
