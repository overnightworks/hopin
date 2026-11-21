from alembic import context

from hopin.stores.schema import metadata

# `hopin.stores.postgres.migrate` hands in the connection that holds the migration lock.
connection = context.config.attributes["connection"]
context.configure(connection=connection, target_metadata=metadata)
with context.begin_transaction():
    context.run_migrations()
