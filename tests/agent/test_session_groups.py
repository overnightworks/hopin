import pytest
from starlette.requests import HTTPConnection
from webauth.config import installed_web_auth_config
from webauth.oidc_login import AccessTokenRefusal, AccessTokenRefusalReason

from hopin.agent.session_groups import SessionGroups

pytestmark = pytest.mark.asyncio


async def test_a_connection_without_a_session_is_a_sign_in_to_repeat(dialed_call):
    groups = SessionGroups(None, None, installed_web_auth_config(dialed_call.client.app))
    connection = HTTPConnection({"type": "websocket", "headers": [], "query_string": b""})

    refusal = await groups.of_connection(connection)

    assert refusal == AccessTokenRefusal(AccessTokenRefusalReason.SIGN_IN_AGAIN)
