import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hopin.web.ice_servers import create_ice_servers_router
from hopin.web.routes import STATIC_DIR

ICE_URL_PATTERN = re.compile(r"(stuns?|turns?):", re.IGNORECASE)


def client_for(stun_server: str) -> TestClient:
    app = FastAPI()
    app.include_router(create_ice_servers_router(stun_server))
    return TestClient(app)


@pytest.mark.parametrize("method", ["get", "head"])
def test_ice_servers_route_serves_the_configured_stun_server(method):
    response = getattr(client_for("stun:stun.example.test:3478"), method)("/api/ice-servers")
    assert response.status_code == 200


def test_ice_servers_route_answers_in_the_browsers_rtc_configuration_shape():
    response = client_for("stun:stun.example.test:3478").get("/api/ice-servers")
    assert response.json() == {"iceServers": [{"urls": ["stun:stun.example.test:3478"]}]}


def test_static_files_name_no_ice_server_url():
    offenders = [
        path.name
        for path in STATIC_DIR.rglob("*")
        if path.is_file() and ICE_URL_PATTERN.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
