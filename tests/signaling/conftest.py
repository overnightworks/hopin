from contextlib import ExitStack
from typing import TYPE_CHECKING

import pytest
import structlog
from fastapi.testclient import TestClient
from webauth.config import installed_web_auth_config

from hopin.app import create_app
from hopin.auth.stores import SignInStores
from hopin.config.constants import (
    WEBSOCKET_PATH,
)
from tests.signaling.support.flow import number_frames_after_the_first
from tests.signaling.support.served_sessions import ServedSessions
from tests.support.signed_in import SeededMember, handshake_headers, seed_member

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.fixture
def stores():
    return SignInStores.in_memory()


@pytest.fixture
def app(sign_in_settings, stores):
    settings = sign_in_settings(max_participants_per_room=3).settings
    return create_app(settings, stores=stores)


@pytest.fixture
def served_sessions(app):
    return ServedSessions(app)


@pytest.fixture
def client(served_sessions):
    with TestClient(served_sessions) as shared_portal_client:
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


@pytest.fixture
def connect(client, member_named):
    with ExitStack() as sessions:
        yield lambda account: number_frames_after_the_first(
            sessions.enter_context(
                client.websocket_connect(WEBSOCKET_PATH, headers=handshake_headers(member_named(account)))
            )
        )
