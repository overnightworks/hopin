"""A fake issuer, the sign-in stores and a starter of members: the surroundings of a session's refresh grants."""

import asyncio
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from starlette.requests import HTTPConnection
from webauth.admission import AppRole
from webauth.ports import AccountKind

from hopin.auth.sign_in import build_relying_party, build_sign_in_configs
from hopin.auth.stores import InMemorySessions, SignInStores
from tests.support.fake_issuer import FakeIssuer
from tests.support.signed_in import cookie_header, seed_member

if TYPE_CHECKING:
    from tests.support.sign_in_settings import SignInSettings

WAIT_CAP_SECONDS = 10
SETTLE_ROUNDS = 5


class StoreSpy:
    """The session store, signalling each stored refresh token, so a test can wait for a worker's write."""

    def __init__(self, sessions: InMemorySessions) -> None:
        self._sessions = sessions
        self.stored_refresh_token = threading.Event()

    def __getattr__(self, name):
        return getattr(self._sessions, name)

    def replace_refresh_token(self, session_id, *, refresh_token):
        stored = self._sessions.replace_refresh_token(session_id, refresh_token=refresh_token)
        self.stored_refresh_token.set()
        return stored


@dataclass
class SessionWorld:
    issuer: FakeIssuer
    stores: SignInStores
    sessions: StoreSpy
    relying_party: object
    web_auth: object

    def starter(self, name="anna", *, with_refresh_token=True, role=None):
        """A socket's connection carrying the session cookie of a member, and the member."""
        account = self.stores.accounts.create_for_subject(
            f"subject-{name}", name, role=role or AppRole.USER, kind=AccountKind.PERSON
        )
        refresh_token = self.issuer.refresh_token_for(account.id) if with_refresh_token else None
        member = seed_member(self.stores, self.web_auth, account=account, refresh_token=refresh_token)
        return connection_with(cookie_header(member.set_cookie_headers)), member

    def grant_types(self):
        return [request.form["grant_type"] for request, _ in self.issuer.exchanges]


def connection_with(cookie):
    headers = [] if cookie is None else [(b"cookie", cookie.encode())]
    return HTTPConnection({"type": "websocket", "headers": headers})


def build_session_world(sign_in: SignInSettings) -> SessionWorld:
    issuer = FakeIssuer(sign_in)
    configs = build_sign_in_configs(sign_in.settings, ())
    stores = SignInStores.in_memory()
    sessions = StoreSpy(stores.sessions)
    relying_party = build_relying_party(configs, issuer.transports)
    return SessionWorld(issuer, stores, sessions, relying_party, configs.web_auth)


async def wait_in_a_worker(event):
    assert await asyncio.to_thread(event.wait, WAIT_CAP_SECONDS)


async def cancelled(task):
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def settle():
    """Lets every task and worker thread that can make progress now do so, so a queued caller has reached its queue."""
    for _ in range(SETTLE_ROUNDS):
        await asyncio.sleep(0)
        await asyncio.to_thread(object)
