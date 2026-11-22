import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from typing import TYPE_CHECKING, NamedTuple
from urllib.parse import urlsplit

import psycopg
import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from hopin.config.constants import HEALTH_PATH, MIGRATION_LOCK_KEY
from hopin.config.settings import DatabaseSettings, ServerSettings
from hopin.main import main, serve_on_migrated_database
from hopin.stores.postgres import create_database_engine
from hopin.stores.schema import metadata
from tests.stores.support.service_gate import ServiceGate
from tests.stores.support.waiting import WAIT_SECONDS, wait_for
from tests.support.page_build import FIXTURE_DIRECTORY, fixture_page_build
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN, TEST_REALM

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI

    from hopin.main import UvicornOptions

pytestmark = pytest.mark.shared_stores

LIST_TABLES = "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name"
LIST_VERSIONS = "SELECT version_num FROM alembic_version"
INSERT_ROBOT_ACCOUNT = "INSERT INTO accounts (id, username, role, kind) VALUES ('subject', 'name', 'member', 'robot')"
INSERT_INTO_A_MISSING_TABLE = "INSERT INTO missing_table (id) VALUES (%s)"
SECRET_PARAMETER = "sentinel-parameter"


class Health(NamedTuple):
    status: HTTPStatus


def health_while_serving(settings: ServerSettings, between: Callable[[], None] = lambda: None) -> list[Health]:
    """`hopin serve`'s production wiring over the real stores and the fixture page build: /health once it serves, and
    again after `between` has run."""
    answers: list[Health] = []

    def ask(client: TestClient) -> None:
        response = client.get(HEALTH_PATH)
        answers.append(Health(HTTPStatus(response.status_code)))

    def probe(application: FastAPI, _options: UvicornOptions) -> None:
        with TestClient(application, base_url=TEST_PUBLIC_ORIGIN) as client:
            ask(client)
            between()
            ask(client)

    serve_on_migrated_database(settings, TEST_REALM, fixture_page_build(), run=probe)
    return answers


class TestMigrate:
    def test_brings_an_empty_database_to_head(self, empty_database, served_environment):
        main(["migrate"])

        assert [table for (table,) in empty_database.rows(LIST_TABLES)] == ["accounts", "alembic_version", "sessions"]
        assert len(empty_database.rows(LIST_VERSIONS)) == 1

    def test_does_not_load_the_schema_the_metadata_describes(self, empty_database, served_environment, monkeypatch):
        monkeypatch.delitem(sys.modules, "hopin.stores.schema")

        main(["migrate"])

        assert "hopin.stores.schema" not in sys.modules

    def test_changes_nothing_at_head(self, migrated_database):
        before = (migrated_database.rows(LIST_VERSIONS), migrated_database.rows(LIST_TABLES))

        main(["migrate"])

        after = (migrated_database.rows(LIST_VERSIONS), migrated_database.rows(LIST_TABLES))
        assert after == before

    def test_runs_concurrent_migrations_one_after_the_other(self, empty_database, served_environment):
        with empty_database.connect() as holder, ThreadPoolExecutor(max_workers=2) as pool:
            holder.execute("SELECT pg_advisory_lock(%s)", [MIGRATION_LOCK_KEY])
            try:
                runs = [pool.submit(main, ["migrate"]) for _ in range(2)]
                wait_for(lambda: empty_database.waiting_for_advisory_lock() == 2)
                assert empty_database.rows(LIST_TABLES) == []
            finally:
                holder.execute("SELECT pg_advisory_unlock(%s)", [MIGRATION_LOCK_KEY])
            assert [run.result(WAIT_SECONDS) for run in runs] == [None, None]

        assert len(empty_database.rows(LIST_VERSIONS)) == 1
        assert len(empty_database.rows(LIST_TABLES)) == 3

    def test_refuses_in_one_line_naming_the_setting_on_a_database_that_does_not_answer(
        self, empty_database, served_environment, monkeypatch, capsys
    ):
        gate = ServiceGate(empty_database.url)
        gate.close()
        monkeypatch.setenv("HOPIN_DATABASE_URL", gate.url)

        with pytest.raises(SystemExit) as refusal:
            main(["migrate"])

        assert refusal.value.code == 1
        output = capsys.readouterr()
        (line,) = output.err.splitlines()
        assert "HOPIN_DATABASE_URL" in line
        assert urlsplit(gate.url).hostname not in output.out + output.err

    def test_leaves_the_schema_the_metadata_describes(self, migrated_database):
        engine = create_database_engine(DatabaseSettings())
        try:
            with engine.connect() as connection:
                assert compare_metadata(MigrationContext.configure(connection), metadata) == []
        finally:
            engine.dispose()

    def test_leaves_an_accounts_table_that_refuses_a_kind_that_is_neither_person_nor_agent(self, migrated_database):
        with migrated_database.connect() as connection, pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(INSERT_ROBOT_ACCOUNT)

    def test_logs_no_statement_and_no_parameter_of_the_database(self, migrated_database, caplog):
        caplog.set_level(logging.DEBUG)
        engine = create_database_engine(DatabaseSettings())
        try:
            with pytest.raises(DBAPIError) as failure, engine.begin() as connection:
                connection.exec_driver_sql(INSERT_INTO_A_MISSING_TABLE, (SECRET_PARAMETER,))
        finally:
            engine.dispose()

        assert SECRET_PARAMETER not in caplog.text + str(failure.value)
        assert [record for record in caplog.records if record.name.startswith("sqlalchemy.engine")] == []


class TestServe:
    def test_refuses_to_start_on_an_unmigrated_database_naming_the_command(
        self, empty_database, served_environment, capsys
    ):
        with pytest.raises(SystemExit) as refusal:
            main(["serve"], build_directory=FIXTURE_DIRECTORY)

        assert refusal.value.code == 1
        (line,) = capsys.readouterr().err.splitlines()
        assert "hopin migrate" in line
        assert empty_database.url not in line
        assert empty_database.password not in line

    def test_is_ready_on_a_migrated_database(self, migrated_database):
        assert [answer.status for answer in health_while_serving(ServerSettings())] == [HTTPStatus.OK, HTTPStatus.OK]

    def test_is_not_ready_while_postgres_does_not_answer_and_says_nothing_of_it(
        self, migrated_database, sign_in_settings, redis_url, capsys
    ):
        gate = ServiceGate(migrated_database.url)
        try:
            settings = sign_in_settings(
                redis_url=redis_url, **{**migrated_database.settings, "database_url": gate.url}
            ).settings

            answers = health_while_serving(settings, between=gate.close)
        finally:
            gate.close()

        assert [answer.status for answer in answers] == [HTTPStatus.OK, HTTPStatus.SERVICE_UNAVAILABLE]
        output = capsys.readouterr()
        records = [json.loads(line) for line in output.out.splitlines() if line.startswith("{")]
        (refusal,) = (record for record in records if record["event"] == "database.refused")
        assert refusal["reason"] == "not_answering"
        assert urlsplit(gate.url).hostname not in output.out + output.err
        # The password may equal the user, and in CI both are "postgres", which the logger's own name
        # (hopin.stores.postgres) contains.
        assert migrated_database.password not in str({key: value for key, value in refusal.items() if key != "logger"})
