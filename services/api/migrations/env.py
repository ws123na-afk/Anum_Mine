from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from logging.config import fileConfig

from alembic import context
from alembic.runtime.migration import MigrationContext
from sqlalchemy import engine_from_config, pool

from anum_api.db.models import Base
from anum_api.settings import settings

config = context.config
# configparser reads % as interpolation: a URL with a percent-encoded password or option
# must be escaped, or the migration stops before it connects.
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# How long a migration statement waits for a lock before it fails.
MIGRATION_LOCK_TIMEOUT = "5s"
_ALEMBIC_AUTOCOMMIT_BLOCK = MigrationContext.autocommit_block


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


@contextmanager
def _autocommit_block_without_lock_timeout(migration_context: MigrationContext) -> Iterator[None]:
    """`CREATE INDEX CONCURRENTLY` waits for every older transaction to finish, and that
    wait is a lock wait: under the timeout it would fail after 5 s next to any long query
    and leave an INVALID index behind, which `IF NOT EXISTS` then skips. It blocks nobody
    while it waits, so autocommit blocks run without the timeout."""
    with _ALEMBIC_AUTOCOMMIT_BLOCK(migration_context):
        connection = migration_context.connection
        timed = connection is not None and connection.dialect.name == "postgresql"
        if timed:
            connection.exec_driver_sql("SET lock_timeout = 0")
        try:
            yield
        finally:
            if timed and not connection.closed and not connection.invalidated:
                connection.exec_driver_sql(f"SET lock_timeout = '{MIGRATION_LOCK_TIMEOUT}'")


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # A migration that needs a lock held by a long query or an open transaction must
        # fail fast: while it waits, every later query on that table queues behind it and
        # the API stops answering. Session-level, before Alembic's transaction begins, so
        # it covers the whole run; a failed run is rolled back and simply run again
        # (docs/deployment.md#migrations).
        if connection.dialect.name == "postgresql":
            connection.exec_driver_sql(f"SET lock_timeout = '{MIGRATION_LOCK_TIMEOUT}'")
            connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata)
        migration_context = context.get_context()
        # On this run's context only: nothing is patched on Alembic's class.
        migration_context.autocommit_block = partial(_autocommit_block_without_lock_timeout, migration_context)  # type: ignore[method-assign]

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
