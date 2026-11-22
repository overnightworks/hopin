"""HopIn's Postgres accounts and sessions where webauth's store checks leave a duty open: a write to an account that
exists, the sweep of expired sessions and the erasure of one person."""

import threading
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from webauth.ports import AccountKind
from webauth.postgres import SessionStoreError

from hopin.stores.accounts import PostgresSubjectUserStore
from hopin.stores.identity import ErasedIdentity

if TYPE_CHECKING:
    from tests.stores.support.postgres_identity import PostgresStores

pytestmark = pytest.mark.shared_stores

SUBJECT = "subject"
SESSION_LIFETIME = timedelta(hours=1)
OTHER_SUBJECT = "other"
LOCK_WAIT_SECONDS = 10
LOCK_POLL_SECONDS = 0.02
WAITING_ADVISORY_LOCKS = """
    SELECT count(*) FROM pg_locks
    WHERE locktype = 'advisory' AND NOT granted
      AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
"""


def seed_session(stores: PostgresStores, session_id: str, expires_at: datetime, subject: str = SUBJECT) -> None:
    stores.sessions.create(
        subject,
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


def held_by(stores: PostgresStores, subject: str) -> tuple[bool, int]:
    """Whether the subject's account exists and how many sessions it holds."""
    records = stores.records_of(subject)
    return records.account is not None, len(records.sessions)


@pytest.fixture
def two_persons(seeded_account: PostgresStores) -> PostgresStores:
    """The subject with two sessions and another person with one."""
    seeded_account.accounts.create_for_subject(OTHER_SUBJECT, "ben", "member", AccountKind.PERSON)
    expires_at = datetime.now(UTC) + SESSION_LIFETIME
    seed_session(seeded_account, "first", expires_at)
    seed_session(seeded_account, "second", expires_at)
    seed_session(seeded_account, "others", expires_at, OTHER_SUBJECT)
    return seeded_account


def test_an_erasure_removes_the_account_and_its_sessions_and_counts_them(two_persons: PostgresStores) -> None:
    erased = two_persons.erase(SUBJECT)

    assert erased == ErasedIdentity(accounts=1, sessions=2)
    assert held_by(two_persons, SUBJECT) == (False, 0)


def test_an_erasure_leaves_another_persons_rows(two_persons: PostgresStores) -> None:
    two_persons.erase(SUBJECT)

    assert held_by(two_persons, OTHER_SUBJECT) == (True, 1)


def test_an_erasure_of_an_unknown_subject_counts_zeros_and_creates_nothing(two_persons: PostgresStores) -> None:
    erased = two_persons.erase("ghost")

    assert (erased, held_by(two_persons, "ghost")) == (ErasedIdentity(accounts=0, sessions=0), (False, 0))


def test_a_second_erasure_counts_zeros(two_persons: PostgresStores) -> None:
    two_persons.erase(SUBJECT)

    assert two_persons.erase(SUBJECT) == ErasedIdentity(accounts=0, sessions=0)


def test_a_database_failure_after_the_sessions_went_deletes_nothing_and_a_rerun_completes(
    two_persons: PostgresStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_delete(self: PostgresSubjectUserStore, user_id: str) -> bool:
        raise OperationalError("DELETE", {}, Exception())

    with monkeypatch.context() as failing:
        failing.setattr(PostgresSubjectUserStore, "delete", failing_delete)
        with pytest.raises(SessionStoreError):
            two_persons.erase(SUBJECT)

    assert held_by(two_persons, SUBJECT) == (True, 2)
    assert two_persons.erase(SUBJECT) == ErasedIdentity(accounts=1, sessions=2)


def wait_until_an_entry_waits_for_the_lock(stores: PostgresStores) -> None:
    waiting = threading.Event()
    deadline = threading.Event()
    with stores.engine.connect() as connection:
        for _ in range(int(LOCK_WAIT_SECONDS / LOCK_POLL_SECONDS)):
            if connection.execute(text(WAITING_ADVISORY_LOCKS)).scalar_one() > 0:
                waiting.set()
                break
            deadline.wait(LOCK_POLL_SECONDS)
    assert waiting.is_set(), "no entry waited for the sign-in lock"


def test_an_erasure_waits_for_an_open_sign_in_entry_and_then_removes_what_the_entry_wrote(
    seeded_account: PostgresStores,
) -> None:
    erased: list[ErasedIdentity] = []
    with seeded_account.sign_in_transaction() as entry:
        erasing = threading.Thread(target=lambda: erased.append(seeded_account.erase(SUBJECT)))
        erasing.start()
        wait_until_an_entry_waits_for_the_lock(seeded_account)
        assert erased == []
        entry.sessions.create(
            SUBJECT,
            session_id="signed-in-meanwhile",
            now=datetime.now(UTC),
            expires_at=datetime.now(UTC) + SESSION_LIFETIME,
            refresh_token=None,
            id_token=None,
            account_language=None,
        )
    erasing.join(LOCK_WAIT_SECONDS)

    assert erased == [ErasedIdentity(accounts=1, sessions=1)]
    assert held_by(seeded_account, SUBJECT) == (False, 0)


def test_a_dry_run_reads_while_a_sign_in_entry_holds_the_lock(two_persons: PostgresStores) -> None:
    with two_persons.sign_in_transaction():
        counted = held_by(two_persons, SUBJECT)

    assert counted == (True, 2)
