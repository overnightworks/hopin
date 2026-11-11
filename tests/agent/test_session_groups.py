import pytest
from starlette.requests import HTTPConnection
from webauth.config import installed_web_auth_config
from webauth.oidc_login import AccessTokenRefusal, AccessTokenRefusalReason

from hopin.agent.session_groups import SessionGroups
from hopin.auth.sign_in import build_relying_party, build_sign_in_configs
from tests.support.fake_issuer import FakeIssuer
from tests.support.signed_in import cookie_header, new_account, seed_member
from tests.support.stores import single_process_stores

pytestmark = pytest.mark.asyncio


async def test_a_connection_without_a_session_is_a_sign_in_to_repeat(dialed_call):
    groups = SessionGroups(None, None, installed_web_auth_config(dialed_call.client.app))
    connection = HTTPConnection({"type": "websocket", "headers": [], "query_string": b""})

    refusal = await groups.of_connection(connection)

    assert refusal == AccessTokenRefusal(AccessTokenRefusalReason.SIGN_IN_AGAIN)


async def test_a_session_whose_refresh_token_was_already_used_still_reads_its_groups(sign_in_settings):
    sign_in = sign_in_settings()
    issuer = FakeIssuer(sign_in)
    configs = build_sign_in_configs(sign_in.settings, ())
    stores = single_process_stores()
    groups = SessionGroups(build_relying_party(configs, issuer.transports), stores.sessions, configs.web_auth)
    account = new_account(stores, configs.web_auth)
    issuer.grant_groups(account.id, ("/scenario/one",))
    member = seed_member(stores, configs.web_auth, account=account, refresh_token=issuer.refresh_token_for(account.id))
    connection = HTTPConnection(
        {"type": "websocket", "headers": [(b"cookie", cookie_header(member.set_cookie_headers).encode())]}
    )
    used = stores.sessions.load(member.session.id).refresh_token
    await groups.of_connection(connection)
    stores.sessions.replace_refresh_token(member.session.id, refresh_token=used)

    assert await groups.of_connection(connection) == frozenset({"/scenario/one"})
