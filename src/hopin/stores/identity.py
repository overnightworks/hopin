"""The one port `create_app` takes for accounts and sessions: the transactions webauth's routes and gate enter, the
person's records for the export and the expiry sweep's delete. Production builds it over the Postgres engine
(`postgres.py`); only the in-thread tests build it over webauth's single-process stores."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from datetime import datetime

    from webauth.ports import SessionRecordStore, SignInStores

    from hopin.stores.accounts import AccountRecord


@dataclass(frozen=True)
class SessionTimes:
    """One stored session without its id and without any token."""

    started_at: datetime
    expires_at: datetime
    holds_refresh_token: bool
    holds_id_token: bool


@dataclass(frozen=True)
class PersonRecords:
    """One subject's account, if any, and its sessions, oldest first."""

    account: AccountRecord | None
    sessions: tuple[SessionTimes, ...]


class IdentityStores(Protocol):
    def sign_in_transaction(self) -> AbstractContextManager[SignInStores]:
        """One serialized sign-in entry, webauth's `SignInTransaction`."""

    def session_transaction(self) -> AbstractContextManager[SessionRecordStore]:
        """One transaction around a session read, webauth's `SessionTransaction`."""

    def records_of(self, subject: str) -> PersonRecords:
        """What is stored about the subject, for the export."""

    def delete_expired(self, at: datetime) -> int:
        """Deletes every session that ended before `at` and returns how many went."""
