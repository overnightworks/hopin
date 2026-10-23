import secrets
import threading
from datetime import UTC, datetime, timedelta

import pytest
import structlog.testing
from pydantic import SecretStr
from webauth.ports import AccountEvent, AccountEventKind, AuditSink, SessionRecordStore, SubjectUserStore, WriteLock

from hopin.auth.stores import (
    InMemoryAccounts,
    InMemorySessions,
    LoggingAuditSink,
    PersonRecords,
    ProcessWriteLock,
    SessionTimes,
    SignInStores,
    StoredAccount,
)
from tests.signed_in import new_id_token

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=12)


@pytest.fixture
def accounts() -> InMemoryAccounts:
    return InMemoryAccounts()


@pytest.fixture
def sessions(accounts: InMemoryAccounts) -> InMemorySessions:
    return InMemorySessions(accounts, threading.Lock())


def test_stores_satisfy_the_webauth_ports(accounts, sessions):
    ports: tuple[SubjectUserStore, SessionRecordStore, WriteLock, AuditSink] = (
        accounts,
        sessions,
        ProcessWriteLock(),
        LoggingAuditSink(),
    )
    assert len(ports) == 4


def test_in_memory_sign_in_stores_load_a_session_for_an_account_created_through_them():
    stores = SignInStores.in_memory()
    account = stores.accounts.create_for_subject("subject", "name", "user")
    session = stores.sessions.create(account.id, now=NOW, expires_at=LATER, refresh_token=None, id_token=new_id_token())
    assert stores.sessions.load(session.id) == session
    assert stores.sessions.load(session.id).user == account


class TestSessionCreation:
    def test_create_mints_distinct_url_safe_ids_of_43_characters(self, accounts, sessions):
        user = accounts.create_for_subject("subject", "name", "user")
        created = [
            sessions.create(user.id, now=NOW, expires_at=LATER, refresh_token=None, id_token=new_id_token())
            for _ in range(1000)
        ]
        assert len({session.id for session in created}) == 1000
        assert {len(session.id) for session in created} == {43}
        assert all(
            set(session.id) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
            for session in created
        )

    def test_create_stores_the_given_instants_and_the_account(self, accounts, sessions):
        user = accounts.create_for_subject("subject", "name", "user")
        session = sessions.create(user.id, now=NOW, expires_at=LATER, refresh_token=None, id_token=new_id_token())
        assert (session.created_at, session.expires_at, session.user) == (NOW, LATER, user)

    def test_create_drops_a_session_expired_before_now_and_keeps_one_expiring_at_now(self, accounts, sessions):
        user = accounts.create_for_subject("subject", "name", "user")
        expired = sessions.create(
            user.id,
            now=NOW - timedelta(hours=2),
            expires_at=NOW - timedelta(seconds=1),
            refresh_token=None,
            id_token=new_id_token(),
        )
        expiring_now = sessions.create(
            user.id, now=NOW - timedelta(hours=2), expires_at=NOW, refresh_token=None, id_token=new_id_token()
        )
        sessions.create(user.id, now=NOW, expires_at=LATER, refresh_token=None, id_token=new_id_token())
        assert sessions.load(expired.id) is None
        assert sessions.load(expiring_now.id) is not None


class TestExpiredSessionSweep:
    def test_delete_expired_removes_only_sessions_expired_before_now_and_answers_the_count(self, accounts, sessions):
        user = accounts.create_for_subject("subject", "name", "user")
        created_at = NOW - timedelta(hours=13)
        expired = sessions.create(
            user.id, now=created_at, expires_at=NOW - timedelta(hours=1), refresh_token=None, id_token=new_id_token()
        )
        expiring_now = sessions.create(
            user.id, now=created_at, expires_at=NOW, refresh_token=None, id_token=new_id_token()
        )
        live = sessions.create(user.id, now=created_at, expires_at=LATER, refresh_token=None, id_token=new_id_token())
        assert sessions.delete_expired(NOW) == 1
        survivors = [sessions.load(session.id) is not None for session in (expired, expiring_now, live)]
        assert survivors == [False, True, True]


class TestSessionRenewalAndEnd:
    def test_touch_of_an_unknown_id_answers_false_and_creates_nothing(self, sessions):
        assert sessions.touch("unknown", expires_at=LATER) is False
        assert sessions.load("unknown") is None

    def test_touch_of_a_known_id_writes_the_expiry_and_keeps_created_at(self, accounts, sessions):
        user = accounts.create_for_subject("subject", "name", "user")
        session = sessions.create(user.id, now=NOW, expires_at=LATER, refresh_token=None, id_token=new_id_token())
        renewed_expiry = LATER + timedelta(hours=1)
        assert sessions.touch(session.id, expires_at=renewed_expiry) is True
        loaded = sessions.load(session.id)
        assert (loaded.created_at, loaded.expires_at) == (NOW, renewed_expiry)

    def test_delete_of_an_unknown_id_is_a_no_op(self, sessions):
        sessions.delete("unknown")

    def test_delete_for_user_answers_the_count_and_leaves_other_users(self, accounts, sessions):
        first = accounts.create_for_subject("first", "name", "user")
        second = accounts.create_for_subject("second", "name", "user")
        ended = [
            sessions.create(first.id, now=NOW, expires_at=LATER, refresh_token=None, id_token=new_id_token())
            for _ in range(2)
        ]
        others = sessions.create(second.id, now=NOW, expires_at=LATER, refresh_token=None, id_token=new_id_token())
        assert sessions.delete_for_user(first.id) == 2
        assert [sessions.load(session.id) for session in ended] == [None, None]
        assert sessions.load(others.id) is not None
        assert (accounts.get(first.id), accounts.get(second.id)) == (None, second)

    def test_delete_for_user_removes_the_account_also_when_no_session_was_left(self, accounts, sessions):
        user = accounts.create_for_subject("subject", "name", "user")
        assert sessions.delete_for_user(user.id) == 0
        assert accounts.get(user.id) is None

    def test_delete_for_user_of_an_unknown_subject_answers_zero_and_changes_nothing(self, accounts, sessions):
        other = accounts.create_for_subject("other", "name", "user")
        assert sessions.delete_for_user("ghost") == 0
        assert accounts.get(other.id) == other


class GuardProbingAccounts(InMemoryAccounts):
    """Before an account read answers, tries to run an erasure on a thread that has the sessions guard to itself.

    The erasure only gets through when the read runs outside the guard; the probe then reports it.
    """

    def __init__(self, guard: threading.Lock) -> None:
        super().__init__()
        self._sessions_guard = guard
        self.sessions: InMemorySessions | None = None
        self.victim: str | None = None
        self.read_ran_outside_the_guard = False
        self.removal_ran_outside_the_guard = False

    def remove(self, user_id: str) -> None:
        if self._sessions_guard.acquire(blocking=False):
            self._sessions_guard.release()
            self.removal_ran_outside_the_guard = True
        super().remove(user_id)

    def existing(self, user_id: str) -> StoredAccount:
        if self._sessions_guard.acquire(blocking=False):
            self._sessions_guard.release()
            self.read_ran_outside_the_guard = True
            assert self.sessions is not None
            assert self.victim is not None
            self.sessions.delete_for_user(self.victim)
        return super().existing(user_id)


class TestRemovalAgainstJoins:
    @pytest.fixture
    def probed(self):
        guard = threading.Lock()
        accounts = GuardProbingAccounts(guard)
        sessions = InMemorySessions(accounts, guard)
        accounts.sessions = sessions
        accounts.victim = "anna"
        accounts.create_for_subject("anna", "anna", "user")
        return accounts, sessions

    def test_load_joins_the_account_inside_the_guard_so_an_erasure_never_meets_a_half_joined_row(self, probed):
        accounts, sessions = probed
        session = sessions.create(
            "anna", now=NOW, expires_at=LATER, refresh_token=SecretStr("refresh"), id_token=new_id_token()
        )
        accounts.read_ran_outside_the_guard = False

        loaded = sessions.load(session.id)
        sessions.delete_for_user("anna")

        assert loaded == session
        assert not accounts.read_ran_outside_the_guard
        assert sessions.load(session.id) is None
        assert accounts.get("anna") is None

    def test_create_checks_the_account_inside_the_guard_so_no_session_outlives_it(self, probed):
        accounts, sessions = probed

        session = sessions.create(
            "anna", now=NOW, expires_at=LATER, refresh_token=SecretStr("refresh"), id_token=new_id_token()
        )
        assert not accounts.read_ran_outside_the_guard
        assert sessions.load(session.id) == session
        assert sessions.delete_for_user("anna") == 1

        assert sessions.load(session.id) is None
        assert accounts.get("anna") is None

    def test_delete_for_user_removes_the_account_inside_the_guard(self, probed):
        accounts, sessions = probed

        sessions.delete_for_user("anna")

        assert accounts.get("anna") is None
        assert not accounts.removal_ran_outside_the_guard

    def test_create_for_a_removed_account_raises_and_stores_no_session(self, accounts, sessions):
        accounts.create_for_subject("anna", "anna", "user")
        sessions.delete_for_user("anna")

        refresh_token = SecretStr("refresh")
        id_token = new_id_token()
        with pytest.raises(KeyError):
            sessions.create("anna", now=NOW, expires_at=LATER, refresh_token=refresh_token, id_token=id_token)

        assert sessions.person_records("anna").sessions == ()

    @pytest.mark.parametrize(("keep_newest", "kept_ages"), [(1, [2]), (0, [])])
    def test_prune_overflow_keeps_the_newest_by_created_at(self, accounts, sessions, keep_newest, kept_ages):
        user = accounts.create_for_subject("subject", "name", "user")
        by_age = {
            age: sessions.create(
                user.id, now=NOW + timedelta(minutes=age), expires_at=LATER, refresh_token=None, id_token=new_id_token()
            )
            for age in (0, 1, 2)
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
        session = sessions.create(user.id, now=NOW, expires_at=LATER, refresh_token=None, id_token=new_id_token())
        accounts.set_role(user.id, "admin")
        assert sessions.load(session.id).user.role == "admin"


def test_no_record_shows_a_session_id_in_its_repr(accounts, sessions):
    user = accounts.create_for_subject("subject", "name", "user")
    created = sessions.create(user.id, now=NOW, expires_at=LATER, refresh_token=None, id_token=new_id_token())
    shown = repr(created) + repr(sessions.load(created.id))
    assert created.id not in shown


class TestRefreshToken:
    def test_a_session_created_with_a_refresh_token_loads_with_it(self, accounts, sessions):
        user = accounts.create_for_subject("subject", "name", "user")
        token = SecretStr(secrets.token_urlsafe())
        created = sessions.create(user.id, now=NOW, expires_at=LATER, refresh_token=token, id_token=new_id_token())
        assert created.refresh_token == token
        assert sessions.load(created.id).refresh_token == token

    def test_a_session_created_without_one_loads_without_it(self, accounts, sessions):
        user = accounts.create_for_subject("subject", "name", "user")
        created = sessions.create(user.id, now=NOW, expires_at=LATER, refresh_token=None, id_token=new_id_token())
        assert sessions.load(created.id).refresh_token is None

    def test_replace_on_a_live_session_stores_the_new_token(self, accounts, sessions):
        user = accounts.create_for_subject("subject", "name", "user")
        created = sessions.create(user.id, now=NOW, expires_at=LATER, refresh_token=None, id_token=new_id_token())
        token = SecretStr(secrets.token_urlsafe())
        assert sessions.replace_refresh_token(created.id, refresh_token=token) is True
        assert sessions.load(created.id).refresh_token == token

    def test_replace_on_an_unknown_or_deleted_session_answers_false_and_creates_nothing(self, accounts, sessions):
        user = accounts.create_for_subject("subject", "name", "user")
        deleted = sessions.create(user.id, now=NOW, expires_at=LATER, refresh_token=None, id_token=new_id_token())
        sessions.delete(deleted.id)
        for session_id in (deleted.id, secrets.token_urlsafe()):
            replaced = sessions.replace_refresh_token(session_id, refresh_token=SecretStr(secrets.token_urlsafe()))
            assert replaced is False
            assert sessions.load(session_id) is None

    def test_no_repr_shows_the_token_value(self, accounts, sessions):
        user = accounts.create_for_subject("subject", "name", "user")
        value = secrets.token_urlsafe()
        created = sessions.create(
            user.id, now=NOW, expires_at=LATER, refresh_token=SecretStr(value), id_token=new_id_token()
        )
        shown = repr(created) + repr(sessions.load(created.id)) + repr(sessions) + repr(vars(sessions))
        assert value not in shown


class TestIdToken:
    def test_a_session_loads_with_the_id_token_it_was_created_with(self, accounts, sessions):
        user = accounts.create_for_subject("subject", "name", "user")
        token = new_id_token()
        created = sessions.create(user.id, now=NOW, expires_at=LATER, refresh_token=None, id_token=token)
        assert sessions.load(created.id).id_token == token

    def test_touch_and_replacing_the_refresh_token_leave_it_unchanged(self, accounts, sessions):
        user = accounts.create_for_subject("subject", "name", "user")
        token = new_id_token()
        created = sessions.create(user.id, now=NOW, expires_at=LATER, refresh_token=None, id_token=token)
        sessions.touch(created.id, expires_at=LATER + timedelta(hours=1))
        sessions.replace_refresh_token(created.id, refresh_token=SecretStr(secrets.token_urlsafe()))
        assert sessions.load(created.id).id_token == token

    def test_no_repr_shows_the_token_value(self, accounts, sessions):
        user = accounts.create_for_subject("subject", "name", "user")
        token = new_id_token()
        created = sessions.create(user.id, now=NOW, expires_at=LATER, refresh_token=None, id_token=token)
        shown = repr(created) + repr(sessions.load(created.id)) + repr(sessions) + repr(vars(sessions))
        assert token.get_secret_value() not in shown


class TestPersonRecords:
    def test_answers_the_subjects_account_and_session_times_oldest_first_without_an_id_or_a_token(
        self, accounts, sessions
    ):
        anna = accounts.create_for_subject("anna", "anna-name", "user")
        ben = accounts.create_for_subject("ben", "ben-name", "user")
        newest = sessions.create(anna.id, now=NOW, expires_at=LATER, refresh_token=None, id_token=new_id_token())
        oldest = sessions.create(
            anna.id,
            now=NOW - timedelta(hours=1),
            expires_at=LATER,
            refresh_token=SecretStr(secrets.token_urlsafe()),
            id_token=new_id_token(),
        )
        sessions.create(
            ben.id, now=NOW, expires_at=LATER, refresh_token=SecretStr(secrets.token_urlsafe()), id_token=new_id_token()
        )

        records = sessions.person_records("anna")

        assert records.account == anna
        assert records.sessions == (
            SessionTimes(NOW - timedelta(hours=1), LATER, holds_refresh_token=True),
            SessionTimes(NOW, LATER, holds_refresh_token=False),
        )
        assert {oldest.id, newest.id}.isdisjoint(vars(records.sessions[0]).values())
        assert not {"id", "refresh_token"} & set(vars(records.sessions[0]))

    def test_answers_the_account_alone_when_it_has_no_session_and_nothing_for_an_unknown_subject(
        self, accounts, sessions
    ):
        accounts.create_for_subject("anna", "anna-name", "user")

        assert sessions.person_records("anna").sessions == ()
        assert sessions.person_records("ghost") == PersonRecords(account=None, sessions=())
        assert accounts.get("ghost") is None


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
