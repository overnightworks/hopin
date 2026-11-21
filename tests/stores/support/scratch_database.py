"""A database of its own on the Postgres the job provides, created before a test and dropped after it."""

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
from psycopg import sql

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@dataclass(frozen=True)
class ScratchDatabase:
    """HopIn's two database settings for it, and the password for the test's own connections."""

    url: str
    password_file: Path
    password: str

    @property
    def settings(self) -> dict[str, str | Path]:
        return {"database_url": self.url, "database_password_file": self.password_file}

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(self.url, password=self.password, autocommit=True)

    def rows(self, statement: str) -> list[tuple]:
        with self.connect() as connection:
            return connection.execute(statement).fetchall()


@contextlib.contextmanager
def scratch_database(server_url: str, worker_id: str, directory: Path) -> Iterator[ScratchDatabase]:
    """A new empty database on the server, named for the test worker, and dropped on exit with any connection to it."""
    server = urlsplit(server_url)
    name = f"hopin_{worker_id}_{uuid4().hex[:12]}"
    password = server.password or ""
    password_file = directory / "scratch-database-password"
    password_file.write_text(password + "\n")
    database = ScratchDatabase(
        url=server._replace(netloc=f"{server.username}@{server.hostname}:{server.port}", path=f"/{name}").geturl(),
        password_file=password_file,
        password=password,
    )
    with psycopg.connect(server_url, autocommit=True) as administrator:
        administrator.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            yield database
        finally:
            administrator.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
