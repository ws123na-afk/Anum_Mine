"""migrations/env.py against PostgreSQL: a migration never queues the API behind a lock.

`ALTER TABLE` needs an ACCESS EXCLUSIVE lock. While it waits for a long query or an open
transaction, every later query on that table waits behind it, so one slow report during
an update would stop the whole API. env.py sets ``lock_timeout`` to 5 s for the migration
session: the migration fails instead, nothing is changed, and it is run again
(docs/deployment.md#migrations).

The real env.py is copied into a scratch script directory whose two probe revisions live
in their own schema (their own ``alembic_version``), so the application schema and its
revision are never touched.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from anum_api.settings import settings

pytestmark = pytest.mark.database

ENV_PY = Path(__file__).resolve().parents[1] / "migrations" / "env.py"
SCHEMA = "anum_migration_lock_probe"

RECORD_SETTINGS = '''
from alembic import op

revision = "0001_probe_settings"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("create table probe_settings (phase text primary key, value text not null)")
    op.execute("create table probe_target (id integer primary key)")
    op.execute("insert into probe_settings values ('transaction', current_setting('lock_timeout'))")
    with op.get_context().autocommit_block():
        op.execute("insert into probe_settings values ('autocommit', current_setting('lock_timeout'))")
    op.execute("insert into probe_settings values ('afterwards', current_setting('lock_timeout'))")


def downgrade() -> None:
    op.execute("drop table probe_target")
    op.execute("drop table probe_settings")
'''

ALTER_TABLE = '''
from alembic import op

revision = "0002_probe_alter"
down_revision = "0001_probe_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table probe_target add column note text")


def downgrade() -> None:
    op.execute("alter table probe_target drop column note")
'''


@pytest.fixture
def probe(database_engine: Engine, test_database_url: str, tmp_path: Path) -> Iterator[tuple[Config, Engine, str]]:
    scripts = tmp_path / "migrations"
    (scripts / "versions").mkdir(parents=True)
    shutil.copy(ENV_PY, scripts / "env.py")
    (scripts / "versions" / "0001_probe_settings.py").write_text(RECORD_SETTINGS, encoding="utf-8")
    (scripts / "versions" / "0002_probe_alter.py").write_text(ALTER_TABLE, encoding="utf-8")
    url = make_url(test_database_url).update_query_dict({"options": f"-csearch_path={SCHEMA}"})
    probe_url = url.render_as_string(hide_password=False)
    with database_engine.begin() as connection:
        connection.execute(text(f"drop schema if exists {SCHEMA} cascade"))
        connection.execute(text(f"create schema {SCHEMA}"))
    config = Config()
    config.set_main_option("script_location", str(scripts))
    config.set_main_option("sqlalchemy.url", probe_url.replace("%", "%%"))
    engine = create_engine(probe_url, poolclass=NullPool)
    try:
        yield config, engine, probe_url
    finally:
        engine.dispose()
        with database_engine.begin() as connection:
            connection.execute(text(f"drop schema if exists {SCHEMA} cascade"))


def _upgrade(config: Config, database_url: str, revision: str) -> None:
    # env.py reads the URL from the settings, as conftest.run_migration arranges.
    original = settings.database_url
    settings.database_url = database_url
    try:
        command.upgrade(config, revision)
    finally:
        settings.database_url = original


def _revision(engine: Engine) -> str:
    with engine.connect() as connection:
        return connection.execute(text("select version_num from alembic_version")).scalar_one()


def test_migrations_run_with_a_short_lock_timeout_except_in_autocommit_blocks(
    probe: tuple[Config, Engine, str],
) -> None:
    config, engine, url = probe
    _upgrade(config, url, "0001_probe_settings")
    with engine.connect() as connection:
        seen = dict(connection.execute(text("select phase, value from probe_settings")).all())
        # The timeout belongs to the migration's own session, not to the database or role.
        assert connection.execute(text("show lock_timeout")).scalar_one() == "0"
    # 5 s for transactional DDL, before and after an autocommit block. Inside one (CREATE
    # INDEX CONCURRENTLY, which blocks nobody while it waits for older transactions) there
    # is no timeout: a timed-out concurrent build would leave an INVALID index behind.
    assert seen == {"transaction": "5s", "autocommit": "0", "afterwards": "5s"}


def test_a_migration_waiting_for_a_lock_fails_fast_and_changes_nothing(probe: tuple[Config, Engine, str]) -> None:
    config, engine, url = probe
    _upgrade(config, url, "0001_probe_settings")
    with engine.connect() as holder:
        # What a long report or a forgotten open transaction holds on a table.
        holder.execute(text("select count(*) from probe_target"))
        started = time.monotonic()
        with pytest.raises(OperationalError) as failure:
            _upgrade(config, url, "0002_probe_alter")
        waited = time.monotonic() - started
        holder.rollback()
    assert "lock timeout" in str(failure.value).lower()
    # About 5 s: not at once, and never the minutes an unbounded wait would take.
    assert 4 <= waited < 30, waited
    assert _revision(engine) == "0001_probe_settings"
    with engine.connect() as connection:
        columns = connection.execute(
            text("select column_name from information_schema.columns where table_schema = :schema and table_name = 'probe_target'"),
            {"schema": SCHEMA},
        ).scalars().all()
    assert columns == ["id"]
    # Run again once the other transaction is over: it applies.
    _upgrade(config, url, "0002_probe_alter")
    assert _revision(engine) == "0002_probe_alter"
