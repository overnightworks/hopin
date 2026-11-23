from datetime import UTC, datetime

import pytest
from webauth.ports import StoreUnavailableError

from hopin.auth.sign_in import LoggingAuditSink
from hopin.config.settings import DatabaseSettings
from hopin.stores.postgres import PostgresIdentity, create_database_engine

pytestmark = pytest.mark.integration

REFUSING_HOST = "127.0.0.1"
DATABASE_USER = "hopin-user"
DATABASE_PASSWORD = "sentinel-password"


@pytest.fixture
def identity(tmp_path):
    password_file = tmp_path / "database-password"
    password_file.write_text(DATABASE_PASSWORD + "\n")
    engine = create_database_engine(
        DatabaseSettings(
            database_url=f"postgresql://{DATABASE_USER}@{REFUSING_HOST}:1/hopin", database_password_file=password_file
        )
    )
    yield PostgresIdentity(engine, LoggingAuditSink())
    engine.dispose()


def enter_sign_in(identity):
    with identity.sign_in_transaction():
        pass


def enter_session_read(identity):
    with identity.session_transaction():
        pass


@pytest.mark.parametrize(
    "call",
    [
        enter_sign_in,
        enter_session_read,
        lambda identity: identity.records_of("subject"),
        lambda identity: identity.delete_expired(datetime.now(UTC)),
    ],
    ids=["sign-in entry", "session read", "records", "expiry sweep"],
)
def test_a_database_that_cannot_be_reached_raises_the_unavailable_error_and_leaks_no_connection_detail(identity, call):
    with pytest.raises(StoreUnavailableError) as refusal:
        call(identity)

    details = (REFUSING_HOST, DATABASE_USER, DATABASE_PASSWORD)
    leaked = [detail for detail in details if detail in str(refusal.value)]
    assert (leaked, refusal.value.__cause__) == ([], None)
