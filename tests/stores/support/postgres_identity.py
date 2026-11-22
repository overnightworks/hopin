"""HopIn's accounts and sessions over a database of the test's own, with the stores that seed it."""

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from webauth.postgres import PostgresSessionStore

from hopin.auth.sign_in import LoggingAuditSink
from hopin.config.settings import DatabaseSettings
from hopin.stores.accounts import PostgresSubjectUserStore
from hopin.stores.postgres import PostgresIdentity, create_database_engine, migrate
from hopin.stores.schema import sessions
from tests.stores.support.scratch_database import ScratchDatabase, scratch_database

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from sqlalchemy import Engine
    from webauth.ports import AuditSink


POSTGRES_URL_VARIABLE = "HOPIN_TEST_POSTGRES_URL"


@pytest.fixture
def empty_database(worker_id: str, tmp_path: Path) -> Iterator[ScratchDatabase]:
    """A database of this test's own on the Postgres the job provides, with no table in it."""
    server_url = os.environ.get(POSTGRES_URL_VARIABLE)
    if server_url is None:
        pytest.fail(
            f"{POSTGRES_URL_VARIABLE} names the Postgres these tests run against (the Shared stores job sets it)"
        )
    with scratch_database(server_url, worker_id, tmp_path) as database:
        yield database


@dataclass(frozen=True)
class PostgresStores(PostgresIdentity):
    """Production's identity over the test's database, with the accounts and sessions that commit each call, which
    seed it. The seeding connection is the test's own and is never used by the app's threads."""

    accounts: PostgresSubjectUserStore
    sessions: PostgresSessionStore


def migrated_engine(database: ScratchDatabase) -> Engine:
    engine = create_database_engine(DatabaseSettings(**database.settings))
    migrate(engine)
    return engine


@pytest.fixture
def postgres_stores_with(empty_database: ScratchDatabase) -> Iterator[Callable[[AuditSink | None], PostgresStores]]:
    """Builds the stores over the test's migrated database for an audit sink, production's unless the test gives one."""
    engine = migrated_engine(empty_database)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as seeding:
        accounts = PostgresSubjectUserStore(seeding)
        seeded_sessions = PostgresSessionStore(seeding, accounts, sessions)
        yield lambda audit: PostgresStores(
            engine, LoggingAuditSink() if audit is None else audit, accounts, seeded_sessions
        )
    engine.dispose()


@pytest.fixture
def postgres_stores(postgres_stores_with: Callable[[AuditSink | None], PostgresStores]) -> PostgresStores:
    return postgres_stores_with(None)
