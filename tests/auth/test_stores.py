import secrets
import threading
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
import structlog.testing
from pydantic import SecretStr
from webauth.ports import AccountEvent, AccountEventKind, AuditSink, SessionRecordStore, SubjectUserStore

from hopin.auth.stores import (
    InMemoryAccounts,
    InMemorySessions,
    InMemorySignInTransaction,
    LoggingAuditSink,
    PersonRecords,
    ProcessWriteLock,
    SessionTimes,
    SignInStores,
    StoredAccount,
)
from tests.support.signed_in import new_id_token

pytestmark = pytest.mark.integration

if TYPE_CHECKING:
    from collections.abc import Callable

    from webauth.ports import SignInStores as WebauthSignInStores

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=12)


REAL_RLOCK = threading.RLock


@pytest.fixture
def stores() -> SignInStores:
    return SignInStores.in_memory()


@pytest.fixture
def accounts(stores: SignInStores) -> InMemoryAccounts:
    return stores.accounts


@pytest.fixture
def sessions(stores: SignInStores) -> InMemorySessions:
    return stores.sessions


def test_stores_satisfy_the_webauth_ports(accounts, sessions):
    ports: tuple[SubjectUserStore, SessionRecordStore, AuditSink] = (accounts, sessions, LoggingAuditSink())
    assert len(ports) == 3


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


def guard_is_free_for_another_thread(guard: threading.RLock) -> bool:
    """A re-entrant guard always admits its holder, so the probe asks from a thread that is not the holder."""
    free: list[bool] = []

    def probe() -> None:
        acquired = guard.acquire(blocking=False)
        if acquired:
            guard.release()
        free.append(acquired)

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join()
    return free[0]


class ContentionObservingGuard:
    """A re-entrant guard that reports, through `was_contended`, that a thread had to wait for it."""

    def __init__(self) -> None:
        self._lock = REAL_RLOCK()
        self._holder: int | None = None
        self._depth = 0
        self.was_contended = threading.Event()

    def acquire(self, blocking: bool = True) -> bool:
        acquired = self._lock.acquire(blocking=False)
        if not acquired and blocking:
            self.was_contended.set()
            acquired = self._lock.acquire()
        if acquired:
            self._holder = threading.get_ident()
            self._depth += 1
        return acquired

    def release(self) -> None:
        self._depth -= 1
        if self._depth == 0:
            self._holder = None
        self._lock.release()

    def held_by_current_thread(self) -> bool:
        return self._holder == threading.get_ident()

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *_exception: object) -> None:
        self.release()


class GuardProbingAccounts(InMemoryAccounts):
    """Before an account read answers, tries to run an erasure on a thread that has the sessions guard to itself.

    The erasure only gets through when the read runs outside the guard; the probe then reports it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.sessions: InMemorySessions | None = None
        self.victim: str | None = None
        self.read_ran_outside_the_guard = False
        self.removal_ran_outside_the_guard = False

    def remove(self, user_id: str) -> None:
        if guard_is_free_for_another_thread(self.guard):
            self.removal_ran_outside_the_guard = True
        super().remove(user_id)

    def existing(self, user_id: str) -> StoredAccount:
        if guard_is_free_for_another_thread(self.guard):
            self.read_ran_outside_the_guard = True
            assert self.sessions is not None
            assert self.victim is not None
            self.sessions.delete_for_user(self.victim)
        return super().existing(user_id)


class TestRemovalAgainstJoins:
    @pytest.fixture
    def probed(self):
        accounts = GuardProbingAccounts()
        sessions = InMemorySessions(accounts)
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
        newest = sessions.create(anna.id, now=NOW, expires_at=LATER, refresh_token=None, id_token=None)
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
            SessionTimes(NOW - timedelta(hours=1), LATER, holds_refresh_token=True, holds_id_token=True),
            SessionTimes(NOW, LATER, holds_refresh_token=False, holds_id_token=False),
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


class RecordingAudit:
    def __init__(self) -> None:
        self.events: list[AccountEvent] = []

    def account_event(self, event: AccountEvent) -> None:
        self.events.append(event)


class BlockFailed(Exception):  # noqa: N818
    pass


def note_guard_held_at(store: InMemorySessions, method: str, guard) -> list[bool]:
    """Wraps `store.<method>` to record, at each call, whether the current thread holds the guard."""
    noted: list[bool] = []
    original = getattr(store, method)

    def noting(*arguments):
        noted.append(guard.held_by_current_thread())
        return original(*arguments)

    setattr(store, method, noting)
    return noted


def fail_after(transaction: InMemorySignInTransaction, work: Callable[[WebauthSignInStores], None]) -> None:
    with transaction() as stores:
        work(stores)
        raise BlockFailed


class TestSignInTransaction:
    @pytest.fixture
    def audit(self) -> RecordingAudit:
        return RecordingAudit()

    @pytest.fixture
    def stores(self, monkeypatch) -> SignInStores:
        """The production construction, with the guard it creates swapped for one that reports contention."""
        with monkeypatch.context() as patch:
            patch.setattr(threading, "RLock", ContentionObservingGuard)
            return SignInStores.in_memory()

    @pytest.fixture
    def transaction(self, stores) -> InMemorySignInTransaction:
        return stores.transaction

    @pytest.fixture
    def transaction_with_audit(self, sessions, audit) -> InMemorySignInTransaction:
        return InMemorySignInTransaction(sessions, audit, ProcessWriteLock())

    @pytest.fixture
    def anna_with_a_session(self, accounts, sessions):
        anna = accounts.create_for_subject("anna", "anna-name", "user")
        session = sessions.create(anna.id, now=NOW, expires_at=LATER, refresh_token=None, id_token=new_id_token())
        return anna, session

    def test_a_block_that_raises_restores_the_accounts_and_sessions_of_its_entry(
        self, transaction, accounts, sessions, anna_with_a_session
    ):
        anna, session = anna_with_a_session

        def write(stores: WebauthSignInStores) -> None:
            stores.sessions.delete_for_user(anna.id)
            stores.accounts.create_for_subject("bob", "bob-name", "user")

        with pytest.raises(BlockFailed):
            fail_after(transaction, write)

        assert accounts.get("bob") is None
        assert accounts.get(anna.id) == anna
        assert sessions.load(session.id) == session

    @pytest.fixture
    def while_a_failing_entry_is_open(self, transaction, accounts, sessions, anna_with_a_session):
        """Runs `other` on a second thread while an entry that has written is still open, then fails the entry.

        The entry fails only once the guard reports that the thread is waiting on it, so the thread has met the
        entry's writes before the rollback, and the rollback must run with the guard still held. Returns what
        `other` answered; an error in it is raised here.
        """
        anna, _session = anna_with_a_session

        def run(other: Callable[[], object], entry_work: Callable[[WebauthSignInStores], None] | None = None) -> object:
            answers: list[object] = []
            errors: list[BaseException] = []

            def other_thread() -> None:
                try:
                    answers.append(other())
                except BaseException as error:
                    errors.append(error)

            thread = threading.Thread(target=other_thread, daemon=True)
            guard_held_at_snapshot = note_guard_held_at(sessions, "snapshot", accounts.guard)
            guard_held_at_restore = note_guard_held_at(sessions, "restore", accounts.guard)

            def write_then_wait_for_the_thread(stores: WebauthSignInStores) -> None:
                if entry_work is None:
                    stores.sessions.create(
                        anna.id, now=NOW, expires_at=LATER, refresh_token=None, id_token=new_id_token()
                    )
                else:
                    entry_work(stores)
                thread.start()
                assert accounts.guard.was_contended.wait(timeout=5), "the thread never waited on the guard"

            try:
                with pytest.raises(BlockFailed):
                    fail_after(transaction, write_then_wait_for_the_thread)
            finally:
                if thread.ident is not None:
                    thread.join(timeout=5)
                    assert not thread.is_alive()
            assert guard_held_at_snapshot == [True]
            assert guard_held_at_restore == [True]
            if errors:
                raise errors[0]
            return answers[0]

        return run

    def test_a_rotation_started_during_a_failed_entry_is_kept(
        self, while_a_failing_entry_is_open, sessions, anna_with_a_session
    ):
        _anna, session = anna_with_a_session

        while_a_failing_entry_is_open(
            lambda: sessions.replace_refresh_token(session.id, refresh_token=SecretStr("rotated"))
        )

        assert sessions.load(session.id).refresh_token.get_secret_value() == "rotated"

    def test_a_renewal_started_during_a_failed_entry_is_kept(
        self, while_a_failing_entry_is_open, sessions, anna_with_a_session
    ):
        _anna, session = anna_with_a_session
        renewed = LATER + timedelta(hours=1)

        while_a_failing_entry_is_open(lambda: sessions.touch(session.id, expires_at=renewed))

        assert sessions.load(session.id).expires_at == renewed

    def test_a_sweep_started_during_a_failed_entry_is_kept(
        self, while_a_failing_entry_is_open, sessions, anna_with_a_session
    ):
        _anna, session = anna_with_a_session

        while_a_failing_entry_is_open(lambda: sessions.delete_expired(LATER + timedelta(hours=1)))

        assert sessions.load(session.id) is None

    def test_a_reader_during_a_failed_entry_never_sees_its_provisional_deletion(
        self, while_a_failing_entry_is_open, sessions, anna_with_a_session
    ):
        anna, session = anna_with_a_session

        seen = while_a_failing_entry_is_open(
            lambda: sessions.load(session.id), entry_work=lambda stores: stores.sessions.delete_for_user(anna.id)
        )

        assert seen == session

    def test_a_block_that_exits_cleanly_keeps_what_it_wrote(self, transaction, accounts, sessions, anna_with_a_session):
        _anna, session = anna_with_a_session

        with transaction() as stores:
            stores.sessions.delete(session.id)
            stores.accounts.create_for_subject("bob", "bob-name", "user")

        assert sessions.load(session.id) is None
        assert accounts.get("bob") is not None

    def test_an_audit_event_reaches_the_sink_only_when_the_block_exits_cleanly(self, transaction_with_audit, audit):
        transaction = transaction_with_audit
        event = AccountEvent(kind=AccountEventKind.BACKCHANNEL_LOGOUT, actor_id=None, subject_id="anna", role="user")

        with pytest.raises(BlockFailed):
            fail_after(transaction, lambda stores: stores.audit.account_event(event))
        assert audit.events == []

        with transaction() as stores:
            stores.audit.account_event(event)
        assert audit.events == [event]

    def test_a_sink_that_fails_when_the_events_are_handed_over_rolls_the_entry_back(
        self, accounts, sessions, anna_with_a_session
    ):
        class DownSink:
            def account_event(self, event: AccountEvent) -> None:
                raise BlockFailed

        transaction = InMemorySignInTransaction(sessions, DownSink(), ProcessWriteLock())
        anna, session = anna_with_a_session
        event = AccountEvent(kind=AccountEventKind.BACKCHANNEL_LOGOUT, actor_id=None, subject_id="anna", role="user")

        def end_the_sessions(stores: WebauthSignInStores) -> None:
            stores.sessions.delete_for_user(anna.id)
            stores.audit.account_event(event)

        with pytest.raises(BlockFailed), transaction() as stores:
            end_the_sessions(stores)

        assert sessions.load(session.id) == session

    def test_the_next_entry_starts_after_the_previous_one_left(self, transaction):
        entered: list[str] = []
        first_inside = threading.Event()
        release_first = threading.Event()

        def first() -> None:
            with transaction():
                first_inside.set()
                release_first.wait(timeout=5)
                entered.append("first left")

        def second() -> None:
            first_inside.wait(timeout=5)
            with transaction():
                entered.append("second entered")

        threads = [threading.Thread(target=first), threading.Thread(target=second)]
        for thread in threads:
            thread.start()
        first_inside.wait(timeout=5)
        release_first.set()
        for thread in threads:
            thread.join(timeout=5)

        assert entered == ["first left", "second entered"]


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
