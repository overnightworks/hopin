import pytest
from starlette.requests import HTTPConnection
from webauth.config import installed_web_auth_config
from webauth.oidc_login import AccessTokenRefusal, AccessTokenRefusalReason

from hopin.agent.session_groups import SessionGroups
from tests.game.support.world import build_world

pytestmark = pytest.mark.asyncio


async def test_a_connection_without_a_session_is_a_sign_in_to_repeat(dialed_call):
    groups = SessionGroups(None, None, installed_web_auth_config(dialed_call.client.app))
    connection = HTTPConnection({"type": "websocket", "headers": [], "query_string": b""})

    refusal = await groups.of_connection(connection)

    assert refusal == AccessTokenRefusal(AccessTokenRefusalReason.SIGN_IN_AGAIN)


async def test_a_session_whose_refresh_token_was_already_used_still_reads_its_groups(sign_in_settings):
    world = build_world(sign_in_settings())
    connection, member = world.starter()
    world.issuer.grant_groups(member.account.id, ("/scenario/one",))
    groups = SessionGroups(world.relying_party, world.sessions, world.web_auth)
    used = world.stores.sessions.load(member.session.id).refresh_token
    await groups.of_connection(connection)
    world.stores.sessions.replace_refresh_token(member.session.id, refresh_token=used)

    assert await groups.of_connection(connection) == frozenset({"/scenario/one"})
