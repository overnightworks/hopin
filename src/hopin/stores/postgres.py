"""The Postgres every HopIn process shares: the engine, `hopin migrate`, the start's check that the schema is the
image's head and the readiness check.

The URL names no password and the password file holds only it; neither value reaches a log or an error line: the
engine hides statement parameters and never sets `echo`, which keeps `sqlalchemy.engine` at SQLAlchemy's default WARNING
(its INFO lines print statements and its DEBUG lines rows), and a failure of the database is reported by what failed,
never by the driver's text, which quotes the host and the parameters.
"""

from dataclasses import replace
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from hopin.config.constants import (
    DATABASE_CONNECT_TIMEOUT_SECONDS,
    DATABASE_POOL_OVERFLOW,
    DATABASE_POOL_SIZE,
    DATABASE_POOL_TIMEOUT_SECONDS,
    MIGRATION_LOCK_KEY,
    POSTGRES_DRIVER,
)
from hopin.config.secret_files import StartRefusedError, read_secret_file

if TYPE_CHECKING:
    from hopin.config.settings import DatabaseSettings
    from hopin.stores.shared import SharedStores

logger = structlog.get_logger()

MIGRATIONS_DIRECTORY = Path(__file__).parent / "migrations"


def create_database_engine(settings: DatabaseSettings) -> Engine:
    """The engine on the settings' URL with the password from its file; it connects on first use."""
    password = read_secret_file(settings.database_password_file, "HOPIN_DATABASE_PASSWORD_FILE")
    url = make_url(settings.database_url).set(drivername=POSTGRES_DRIVER, password=password.get_secret_value())
    return create_engine(
        url,
        pool_size=DATABASE_POOL_SIZE,
        max_overflow=DATABASE_POOL_OVERFLOW,
        pool_timeout=DATABASE_POOL_TIMEOUT_SECONDS,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": DATABASE_CONNECT_TIMEOUT_SECONDS},
    )


def _alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIRECTORY))
    return config


@cache
def _head_revision() -> str | None:
    """The newest revision, which the migration files fix for the life of the process."""
    return ScriptDirectory.from_config(_alembic_config()).get_current_head()


def migrate(engine: Engine) -> None:
    """Bring the database to head; concurrent runs wait for one another on a Postgres advisory lock."""
    config = _alembic_config()
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        connection.commit()
        try:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        finally:
            connection.rollback()
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})
            connection.commit()


def require_head(engine: Engine) -> None:
    """Refuses a start on a database that is not at this image's head, in a line that names the command."""
    try:
        with engine.connect() as connection:
            heads = MigrationContext.configure(connection).get_current_heads()
    except SQLAlchemyError:
        raise StartRefusedError("HOPIN_DATABASE_URL names a database that does not answer") from None
    if heads != (_head_revision(),):
        raise StartRefusedError("the database is not at this image's schema; run hopin migrate")


def _database_answers(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.warning("database_not_answering")
        return False
    return True


def with_database_readiness(shared_stores: SharedStores, engine: Engine) -> SharedStores:
    """The stores whose readiness is the shared store's and the database's."""
    return replace(shared_stores, answers=lambda: shared_stores.answers() and _database_answers(engine))
