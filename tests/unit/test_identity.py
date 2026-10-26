import pytest
from starlette.websockets import WebSocket
from webauth.config import installed_web_auth_config

from hopin.app import create_app
from hopin.auth.stores import SignInStores
from hopin.config.constants import WEBSOCKET_PATH
from hopin.rooms.models import Account
from hopin.signaling.identity import SessionIdentityResolver
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN
from tests.support.signed_in import cookie_header, seed_member


def handshake(app, headers: dict[str, str]) -> WebSocket:
    scope = {
        "type": "websocket",
        "app": app,
        "path": WEBSOCKET_PATH,
        "query_string": b"",
        "headers": [(name.encode(), value.encode()) for name, value in headers.items()],
    }
    return WebSocket(scope, receive=None, send=None)


class TestSessionIdentityResolver:
    @pytest.mark.asyncio
    async def test_resolve_answers_the_session_account_and_refuses_a_handshake_without_a_cookie(self, sign_in_settings):
        stores = SignInStores.in_memory()
        app = create_app(sign_in_settings().settings, stores=stores)
        member = seed_member(stores, installed_web_auth_config(app))
        resolver = SessionIdentityResolver(stores.sessions)
        origin = {"origin": TEST_PUBLIC_ORIGIN}

        admitted = await resolver.resolve(
            handshake(app, {**origin, "cookie": cookie_header(member.set_cookie_headers)})
        )
        refused = await resolver.resolve(handshake(app, origin))

        assert admitted == Account(id=member.account.id, name=member.account.username)
        assert refused is None
