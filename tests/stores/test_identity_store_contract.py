"""HopIn's Postgres accounts and sessions against webauth's checks for store adapters."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from webauth.store_contract import STORE_CHECKS, StoreAdapter

from hopin.auth.sign_in import LoggingAuditSink
from hopin.config.settings import DatabaseSettings
from hopin.stores.postgres import PostgresIdentity, create_database_engine

if TYPE_CHECKING:
    from pathlib import Path

    from webauth.ports import SignInTransaction
    from webauth.store_contract import StoreCheck

    from tests.stores.support.postgres_identity import PostgresStores
    from tests.stores.support.scratch_database import ScratchDatabase

pytestmark = pytest.mark.shared_stores


@dataclass(frozen=True)
class SignInTransactionOver:
    """Builds production's sign-in transaction in a process that shares nothing with the test's."""

    database_url: str
    password_file: Path

    def __call__(self) -> SignInTransaction:
        engine = create_database_engine(
            DatabaseSettings(database_url=self.database_url, database_password_file=self.password_file)
        )
        return PostgresIdentity(engine, LoggingAuditSink()).sign_in_transaction


@pytest.mark.parametrize("check", STORE_CHECKS, ids=lambda check: check.name)
def test_the_postgres_stores_keep_the_duty_of_every_port(
    check: StoreCheck, postgres_stores: PostgresStores, empty_database: ScratchDatabase
) -> None:
    check.run(
        StoreAdapter(
            accounts=postgres_stores.accounts,
            sessions=postgres_stores.sessions,
            transaction=postgres_stores.sign_in_transaction,
            transaction_in_another_process=SignInTransactionOver(empty_database.url, empty_database.password_file),
        )
    )
