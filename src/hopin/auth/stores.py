import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import datetime

    from pydantic import SecretStr
    from webauth.ports import AccountEvent

SESSION_ID_BYTES = 32

logger = structlog.get_logger()


@dataclass(frozen=True)
class StoredAccount:
    id: str
    username: str
    role: str


@dataclass(frozen=True)
class StoredSession:
    id: str = field(repr=False)
    user: StoredAccount
    created_at: datetime
    expires_at: datetime
    refresh_token: SecretStr | None


@dataclass(frozen=True)
class SessionTimes:
    started_at: datetime
    expires_at: datetime
    holds_refresh_token: bool


@dataclass(frozen=True)
class PersonRecords:
    account: StoredAccount | None
    sessions: tuple[SessionTimes, ...]


@dataclass(frozen=True)
class _SessionRow:
    id: str = field(repr=False)
    user_id: str
    created_at: datetime
    expires_at: datetime
    refresh_token: SecretStr | None


class InMemoryAccounts:
    """Accounts of one process, keyed by the issuer's subject and compared exactly."""

    def __init__(self) -> None:
        self._by_subject: dict[str, StoredAccount] = {}
        self._guard = threading.Lock()

    def get(self, user_id: str) -> StoredAccount | None:
        with self._guard:
            return self._by_subject.get(user_id)

    def existing(self, user_id: str) -> StoredAccount:
        with self._guard:
            return self._by_subject[user_id]

    def create_for_subject(self, subject: str, username: str, role: str) -> StoredAccount:
        account = StoredAccount(id=subject, username=username, role=role)
        with self._guard:
            self._by_subject[subject] = account
        return account

    def set_role(self, user_id: str, role: str) -> StoredAccount:
        with self._guard:
            existing = self._by_subject[user_id]
            account = StoredAccount(id=existing.id, username=existing.username, role=role)
            self._by_subject[user_id] = account
        return account


class InMemorySessions:
    """Sessions of one process; each record answers its account from the accounts store, as a join would."""

    def __init__(self, accounts: InMemoryAccounts) -> None:
        self._accounts = accounts
        self._rows: dict[str, _SessionRow] = {}
        self._guard = threading.Lock()

    def create(
        self, user_id: str, *, now: datetime, expires_at: datetime, refresh_token: SecretStr | None
    ) -> StoredSession:
        user = self._accounts.existing(user_id)
        row = _SessionRow(
            id=secrets.token_urlsafe(SESSION_ID_BYTES),
            user_id=user_id,
            created_at=now,
            expires_at=expires_at,
            refresh_token=refresh_token,
        )
        with self._guard:
            self._drop_expired(now)
            self._rows[row.id] = row
        return _joined(row, user)

    def delete_expired(self, now: datetime) -> int:
        with self._guard:
            return self._drop_expired(now)

    def _drop_expired(self, now: datetime) -> int:
        expired = [id_ for id_, row in self._rows.items() if row.expires_at < now]
        for session_id in expired:
            del self._rows[session_id]
        return len(expired)

    def load(self, session_id: str) -> StoredSession | None:
        with self._guard:
            row = self._rows.get(session_id)
        return None if row is None else _joined(row, self._accounts.existing(row.user_id))

    def person_records(self, subject: str) -> PersonRecords:
        """What the store holds about one subject, without a session id or token; sessions oldest first."""
        with self._guard:
            rows = sorted(
                (row for row in self._rows.values() if row.user_id == subject), key=lambda row: row.created_at
            )
            account = self._accounts.get(subject)
        sessions = tuple(
            SessionTimes(row.created_at, row.expires_at, holds_refresh_token=row.refresh_token is not None)
            for row in rows
        )
        return PersonRecords(account=account, sessions=sessions)

    def touch(self, session_id: str, *, expires_at: datetime) -> bool:
        with self._guard:
            row = self._rows.get(session_id)
            if row is None:
                return False
            self._rows[session_id] = replace(row, expires_at=expires_at)
            return True

    def replace_refresh_token(self, session_id: str, *, refresh_token: SecretStr) -> bool:
        with self._guard:
            row = self._rows.get(session_id)
            if row is None:
                return False
            self._rows[session_id] = replace(row, refresh_token=refresh_token)
            return True

    def delete(self, session_id: str) -> None:
        with self._guard:
            self._rows.pop(session_id, None)

    def delete_for_user(self, user_id: str) -> int:
        with self._guard:
            ended = [id_ for id_, row in self._rows.items() if row.user_id == user_id]
            for session_id in ended:
                del self._rows[session_id]
        return len(ended)

    def prune_overflow(self, user_id: str, keep_newest: int) -> None:
        with self._guard:
            newest_first = sorted(
                (row for row in self._rows.values() if row.user_id == user_id),
                key=lambda row: row.created_at,
                reverse=True,
            )
            for row in newest_first[keep_newest:]:
                del self._rows[row.id]


def _joined(row: _SessionRow, user: StoredAccount) -> StoredSession:
    return StoredSession(
        id=row.id,
        user=user,
        created_at=row.created_at,
        expires_at=row.expires_at,
        refresh_token=row.refresh_token,
    )


class ProcessWriteLock:
    """Serializes check-then-write operations within one process."""

    def __init__(self) -> None:
        self._guard = threading.Lock()

    @contextmanager
    def hold(self) -> Iterator[None]:
        with self._guard:
            yield


class LoggingAuditSink:
    """Logs each account event by its kind, the issuer's subject and the role, never a username."""

    def account_event(self, event: AccountEvent) -> None:
        logger.info("account_event", kind=event.kind.value, subject_id=event.subject_id, role=event.role)


@dataclass(frozen=True)
class SignInStores:
    accounts: InMemoryAccounts
    sessions: InMemorySessions
    lock: ProcessWriteLock
    audit: LoggingAuditSink

    @classmethod
    def in_memory(cls) -> SignInStores:
        accounts = InMemoryAccounts()
        return cls(
            accounts=accounts, sessions=InMemorySessions(accounts), lock=ProcessWriteLock(), audit=LoggingAuditSink()
        )
