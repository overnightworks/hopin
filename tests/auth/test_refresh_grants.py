import asyncio

import pytest
from webauth.oidc_login import AccessTokenRefusal, AccessTokenRefusalReason

from hopin.auth.refresh_grants import SessionGrants
from tests.game.support.world import build_world, cancelled, wait_in_a_worker

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

GROUPS = ("/group/one",)


@pytest.fixture
def world(sign_in_settings):
    return build_world(sign_in_settings())


@pytest.fixture
def grants(world):
    return SessionGrants(world.relying_party, world.sessions, world.web_auth)


def signed_in(world, name="anna"):
    _, member = world.starter(name)
    world.issuer.grant_groups(member.account.id, GROUPS)
    return member.session.id


async def test_two_overlapping_grants_of_one_session_both_succeed_one_after_the_other(world, grants):
    session_id = signed_in(world)
    hold = world.issuer.hold("refresh_token")

    groups = asyncio.create_task(grants.current_groups(session_id))
    await wait_in_a_worker(hold.parked)
    token = asyncio.create_task(grants.fresh_access_token(session_id))
    hold.release.set()

    assert await groups == GROUPS
    assert not isinstance(await token, AccessTokenRefusal)


async def test_a_cancelled_caller_keeps_the_turn_until_its_worker_has_stored_the_rotated_token(world, grants):
    session_id = signed_in(world)
    hold = world.issuer.hold("refresh_token")

    cancelled_grant = asyncio.create_task(grants.current_groups(session_id))
    await wait_in_a_worker(hold.parked)
    await cancelled(cancelled_grant)
    next_grant = asyncio.create_task(grants.fresh_access_token(session_id))
    hold.release.set()

    assert not isinstance(await next_grant, AccessTokenRefusal)
    assert world.sessions.stored_refresh_token.is_set()


async def test_a_caller_cancelled_before_its_turn_makes_no_grant(world, grants):
    session_id = signed_in(world)
    hold = world.issuer.hold("refresh_token")

    first = asyncio.create_task(grants.current_groups(session_id))
    await wait_in_a_worker(hold.parked)
    await cancelled(asyncio.create_task(grants.fresh_access_token(session_id)))
    hold.release.set()
    await first

    assert world.grant_types().count("refresh_token") == 1


async def test_grants_of_different_sessions_do_not_wait_for_each_other(world, grants):
    held_session, other_session = signed_in(world, "anna"), signed_in(world, "ben")
    hold = world.issuer.hold("refresh_token")

    held = asyncio.create_task(grants.current_groups(held_session))
    await wait_in_a_worker(hold.parked)
    other = asyncio.create_task(grants.current_groups(other_session))
    hold.release.set()

    assert await held == GROUPS
    assert await other == GROUPS


async def test_a_typed_refusal_comes_back_as_a_value_and_does_not_hold_the_next_grant(world, grants):
    session_id = signed_in(world)
    world.issuer.refuses_refresh_grants = True

    refused = await grants.current_groups(session_id)
    refused_again = await grants.fresh_access_token(session_id)

    assert refused == AccessTokenRefusal(AccessTokenRefusalReason.SIGN_IN_AGAIN)
    assert refused_again == refused


async def test_an_unexpected_store_failure_raises_to_the_caller(world):
    session_id = signed_in(world)

    class FailingStore:
        def __getattr__(self, name):
            return getattr(world.sessions, name)

        def replace_refresh_token(self, *_, **__):
            raise OSError("the store is gone")

    failing = SessionGrants(world.relying_party, FailingStore(), world.web_auth)

    with pytest.raises(OSError, match="the store is gone"):
        await failing.current_groups(session_id)
