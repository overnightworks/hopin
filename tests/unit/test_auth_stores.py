from datetime import UTC, datetime, timedelta

import pytest
import structlog.testing
from webauth.ports import AccountEvent, AccountEventKind, AuditSink, SessionRecordStore, SubjectUserStore, WriteLock

from hopin.auth.stores import InMemoryAccounts, InMemorySessions, LoggingAuditSink, ProcessWriteLock

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=12)


@pytest.fixture
def accounts() -> InMemoryAccounts:
    return InMemoryAccounts()


@pytest.fixture
def sessions(accounts: InMemoryAccounts) -> InMemorySessions:
    return InMemorySessions(accounts)


def test_stores_satisfy_the_webauth_ports(accounts, sessions):
    ports: tuple[SubjectUserStore, SessionRecordStore, WriteLock, AuditSink] = (
        accounts,
        sessions,
        ProcessWriteLock(),
        LoggingAuditSink(),
    )
    assert len(ports) == 4


class TestSessionCreation:
    def test_create_mints_distinct_url_safe_ids_of_43_characters(self, accounts, sessions):
        user = accounts.create_for_subject("subject", "name", "user")
        created = [sessions.create(user.id, now=NOW, expires_at=LATER) for _ in range(1000)]
        assert len({session.id for session in created}) == 1000
        assert {len(session.id) for session in created} == {43}
        assert all(
            set(session.id) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
            for session in created
        )

    def test_create_stores_the_given_instants_and_the_account(self, accounts, sessions):
        user = accounts.create_for_subject("subject", "name", "user")
        session = sessions.create(user.id, now=NOW, expires_at=LATER)
        assert (session.created_at, session.expires_at, session.user) == (NOW, LATER, user)

    def test_create_drops_a_session_expired_before_now_and_keeps_one_expiring_at_now(self, accounts, sessions):
        user = accounts.create_for_subject("subject", "name", "user")
        expired = sessions.create(user.id, now=NOW - timedelta(hours=2), expires_at=NOW - timedelta(seconds=1))
        expiring_now = sessions.create(user.id, now=NOW - timedelta(hours=2), expires_at=NOW)
        sessions.create(user.id, now=NOW, expires_at=LATER)
        assert sessions.load(expired.id) is None
        assert sessions.load(expiring_now.id) is not None


class TestSessionRenewalAndEnd:
    def test_touch_of_an_unknown_id_answers_false_and_creates_nothing(self, sessions):
        assert sessions.touch("unknown", expires_at=LATER) is False
        assert sessions.load("unknown") is None

    def test_touch_of_a_known_id_writes_the_expiry_and_keeps_created_at(self, accounts, sessions):
        user = accounts.create_for_subject("subject", "name", "user")
        session = sessions.create(user.id, now=NOW, expires_at=LATER)
        renewed_expiry = LATER + timedelta(hours=1)
        assert sessions.touch(session.id, expires_at=renewed_expiry) is True
        loaded = sessions.load(session.id)
        assert (loaded.created_at, loaded.expires_at) == (NOW, renewed_expiry)

    def test_delete_of_an_unknown_id_is_a_no_op(self, sessions):
        sessions.delete("unknown")

    def test_delete_for_user_answers_the_count_and_leaves_other_users(self, accounts, sessions):
        first = accounts.create_for_subject("first", "name", "user")
        second = accounts.create_for_subject("second", "name", "user")
        ended = [sessions.create(first.id, now=NOW, expires_at=LATER) for _ in range(2)]
        others = sessions.create(second.id, now=NOW, expires_at=LATER)
        assert sessions.delete_for_user(first.id) == 2
        assert [sessions.load(session.id) for session in ended] == [None, None]
        assert sessions.load(others.id) is not None

    @pytest.mark.parametrize(("keep_newest", "kept_ages"), [(1, [2]), (0, [])])
    def test_prune_overflow_keeps_the_newest_by_created_at(self, accounts, sessions, keep_newest, kept_ages):
        user = accounts.create_for_subject("subject", "name", "user")
        by_age = {
            age: sessions.create(user.id, now=NOW + timedelta(minutes=age), expires_at=LATER) for age in (0, 1, 2)
        }
        sessions.prune_overflow(user.id, keep_newest)
        assert [age for age, session in by_age.items() if sessions.load(session.id)] == kept_ages


class TestAccounts:
    def test_get_compares_the_subject_exactly(self, accounts):
        accounts.create_for_subject("Subject", "name", "user")
        assert accounts.get("subject") is None

    def test_create_for_subject_allows_two_subjects_with_one_username(self, accounts):
        first = accounts.create_for_subject("first", "same", "user")
        second = accounts.create_for_subject("second", "same", "user")
        assert (first.username, second.username) == ("same", "same")

    def test_set_role_of_an_unknown_id_raises_key_error(self, accounts):
        with pytest.raises(KeyError):
            accounts.set_role("unknown", "admin")

    def test_set_role_changes_the_role_a_session_loads_with(self, accounts, sessions):
        user = accounts.create_for_subject("subject", "name", "user")
        session = sessions.create(user.id, now=NOW, expires_at=LATER)
        accounts.set_role(user.id, "admin")
        assert sessions.load(session.id).user.role == "admin"


def test_no_record_shows_a_session_id_in_its_repr(accounts, sessions):
    user = accounts.create_for_subject("subject", "name", "user")
    created = sessions.create(user.id, now=NOW, expires_at=LATER)
    shown = repr(created) + repr(sessions.load(created.id))
    assert created.id not in shown


def test_write_lock_can_be_held_twice_one_after_the_other():
    lock = ProcessWriteLock()
    with lock.hold():
        pass
    with lock.hold():
        pass


def test_audit_sink_logs_the_kind_subject_and_role_and_nothing_else():
    with structlog.testing.capture_logs() as logs:
        LoggingAuditSink().account_event(
            AccountEvent(kind=AccountEventKind.USER_CREATED, actor_id=None, subject_id="subject", role="user")
        )
    assert [(log["kind"], log["subject_id"], log["role"]) for log in logs] == [("user_created", "subject", "user")]
