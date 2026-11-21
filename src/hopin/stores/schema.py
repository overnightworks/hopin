"""HopIn's tables as one `MetaData`: the accounts and webauth's session table. The migrations write the same tables out
literally; the drift test in `tests/stores` compares them, so a webauth release that changes its table fails there."""

from sqlalchemy import CheckConstraint, Column, MetaData, Table, Text
from webauth.postgres import session_table

metadata = MetaData()

accounts = Table(
    "accounts",
    metadata,
    # The issuer's `sub`, compared byte for byte.
    Column("id", Text, primary_key=True),
    Column("username", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("kind", Text, nullable=False),
    CheckConstraint("kind IN ('person', 'agent')", name="accounts_kind_is_a_kind"),
)

sessions = session_table(metadata, accounts.c.id)
