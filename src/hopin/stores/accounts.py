"""HopIn's accounts: the `accounts` table behind webauth's `SubjectUserStore`, read and written on the connection of
the caller's transaction, which begins and commits it."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import insert, select, update
from webauth.ports import AccountKind

from hopin.stores.schema import accounts

if TYPE_CHECKING:
    from sqlalchemy import Connection, Row


@dataclass(frozen=True)
class AccountRecord:
    id: str
    username: str
    role: str
    kind: AccountKind


def _record(row: Row[tuple[str, str, str, str]]) -> AccountRecord:
    return AccountRecord(id=row.id, username=row.username, role=row.role, kind=AccountKind(row.kind))


class PostgresSubjectUserStore:
    """Accounts keyed by the issuer's subject, which Postgres compares byte for byte.

    A username is not unique, so no call raises `UsernameTakenError`.
    """

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get(self, user_id: str) -> AccountRecord | None:
        row = self._connection.execute(select(accounts).where(accounts.c.id == user_id)).one_or_none()
        return None if row is None else _record(row)

    def create_for_subject(self, subject: str, username: str, role: str, kind: AccountKind) -> AccountRecord:
        row = self._connection.execute(
            insert(accounts).values(id=subject, username=username, role=role, kind=kind.value).returning(accounts)
        ).one()
        return _record(row)

    def set_role(self, user_id: str, role: str) -> AccountRecord:
        return self._changed(user_id, role=role)

    def set_kind(self, user_id: str, kind: AccountKind) -> AccountRecord:
        return self._changed(user_id, kind=kind.value)

    def _changed(self, user_id: str, **values: str) -> AccountRecord:
        row = self._connection.execute(
            update(accounts).where(accounts.c.id == user_id).values(**values).returning(accounts)
        ).one_or_none()
        if row is None:
            raise LookupError("no account to change")
        return _record(row)
