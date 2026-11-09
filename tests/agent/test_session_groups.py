import asyncio
import threading

import pytest
from starlette.requests import HTTPConnection
from webauth.config import installed_web_auth_config
from webauth.oidc_login import AccessTokenRefusal, AccessTokenRefusalReason

from hopin.agent.session_groups import SessionGroups
from hopin.auth.sign_in import IssuerTransports, build_relying_party, build_sign_in_configs
from hopin.auth.stores import SignInStores
from tests.support.fake_issuer import FakeIssuer
from tests.support.signed_in import new_account, seed_member

OVERLAP_WINDOW_SECONDS = 0.2

pytestmark = pytest.mark.asyncio


async def test_a_connection_without_a_session_is_a_sign_in_to_repeat(dialed_call):
    groups = SessionGroups(None, None, installed_web_auth_config(dialed_call.client.app))
    connection = HTTPConnection({"type": "websocket", "headers": [], "query_string": b""})

    refusal = await groups.of_connection(connection)

    assert refusal == AccessTokenRefusal(AccessTokenRefusalReason.SIGN_IN_AGAIN)


@pytest.fixture
def issuer_and_groups(sign_in_settings):
    """The groups of a session over a fake issuer that revokes a refresh token on use, as Keycloak does."""
    sign_in = sign_in_settings()
    issuer = FakeIssuer(sign_in)
    configs = build_sign_in_configs(sign_in.settings, ())
    stores = SignInStores.in_memory()
    account = new_account(stores, configs.web_auth)
    issuer.grant_groups(account.id, ("/apps/hopin",))
    session = seed_member(
        stores, configs.web_auth, account=account, refresh_token=issuer.refresh_token_for(account.id)
    ).session
    grants_sent = []
    second_grant_sent = threading.Event()

    def post_token_request(request):
        grants_sent.append(request)
        if len(grants_sent) == 2:
            second_grant_sent.set()
        return issuer.post_token_request(request)

    transports = IssuerTransports(post_token_request=post_token_request, fetch_key_set=issuer.fetch_key_set)
    relying_party = build_relying_party(configs, transports)
    return issuer, second_grant_sent, SessionGroups(relying_party, stores.sessions, configs.web_auth), session.id


async def test_two_overlapping_reads_of_one_session_both_see_its_groups(issuer_and_groups):
    issuer, second_grant_sent, groups, session_id = issuer_and_groups
    hold = issuer.hold("refresh_token")
    first = asyncio.create_task(groups.of_session(session_id))
    await asyncio.to_thread(hold.parked.wait)

    second = asyncio.create_task(groups.of_session(session_id))
    await asyncio.to_thread(second_grant_sent.wait, OVERLAP_WINDOW_SECONDS)
    hold.release.set()

    assert await first == await second == frozenset({"/apps/hopin"})
