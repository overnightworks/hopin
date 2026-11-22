"""HopIn's Postgres accounts and sessions where webauth's store checks leave a duty open: a write to an account that
exists, and the sweep of expired sessions."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from webauth.ports import AccountKind

if TYPE_CHECKING:
    from tests.stores.support.postgres_identity import PostgresStores

pytestmark = pytest.mark.shared_stores

SUBJECT = "subject"
SESSION_LIFETIME = timedelta(hours=1)


def seed_session(stores: PostgresStores, session_id: str, expires_at: datetime) -> None:
    stores.sessions.create(
        SUBJECT,
        session_id=session_id,
        now=expires_at - SESSION_LIFETIME,
        expires_at=expires_at,
        refresh_token=None,
        id_token=None,
        account_language=None,
    )


@pytest.fixture
def seeded_account(postgres_stores: PostgresStores) -> PostgresStores:
    postgres_stores.accounts.create_for_subject(SUBJECT, "anna", "member", AccountKind.PERSON)
    return postgres_stores


def test_a_role_written_to_an_account_is_the_role_it_reads_back(seeded_account: PostgresStores) -> None:
    returned = seeded_account.accounts.set_role(SUBJECT, "admin")

    assert (returned.role, seeded_account.accounts.get(SUBJECT)) == ("admin", returned)


def test_a_kind_written_to_an_account_is_the_kind_it_reads_back(seeded_account: PostgresStores) -> None:
    returned = seeded_account.accounts.set_kind(SUBJECT, AccountKind.AGENT)

    assert (returned.kind, seeded_account.accounts.get(SUBJECT)) == (AccountKind.AGENT, returned)


def test_the_sweep_deletes_the_expired_sessions_and_keeps_the_live_ones(seeded_account: PostgresStores) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    seed_session(seeded_account, "expired", now - timedelta(minutes=1))
    seed_session(seeded_account, "live", now + timedelta(minutes=1))

    deleted = seeded_account.delete_expired(now)

    with seeded_account.session_transaction() as sessions:
        kept = [sessions.load(session_id) is not None for session_id in ("expired", "live")]
    assert (deleted, kept) == (1, [False, True])
