"""Schema and lifecycle contract checks for live-agent meetings.

Real PostgreSQL RLS and concurrent-lease behavior remain release-gated and
must be exercised by database integration tests before deployment.
"""
from pathlib import Path
import importlib.util

from anum_api.tenant_lifecycle_tables import BY_NAME, EXPORTED_TABLES, PURGED_TABLES

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "versions" / "0098_agent_live_meetings.py"


def test_live_meeting_migration_chains_after_office_registry():
    module_name = "meeting_migration_contract"
    spec = importlib.util.spec_from_file_location(module_name, MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0098_agent_live_meetings"
    assert module.down_revision == "0097_office_spaces"
    assert module.TABLES == (
        "agent_meeting_sessions",
        "agent_meeting_participants",
        "agent_meeting_turns",
        "agent_meeting_turn_leases",
    )


def test_meeting_tables_exported_and_purged_in_fk_safe_order():
    tables = (
        "agent_meeting_turn_leases",
        "agent_meeting_turns",
        "agent_meeting_participants",
        "agent_meeting_sessions",
    )
    names = [t.name for t in PURGED_TABLES]
    export_names = {t.name for t in EXPORTED_TABLES}
    for table in tables:
        assert table in BY_NAME
        assert BY_NAME[table].domain == "meetings"
        assert BY_NAME[table].workspace_column == "workspace_id"
        assert table in names
        assert table in export_names
    positions = [names.index(t) for t in tables]
    assert positions == sorted(positions)


def test_all_children_use_scoped_foreign_keys_and_forced_rls():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "force row level security" in sql
    assert "tenant_id = nullif(current_setting('anum.tenant_id', true)" in sql
    assert "workspace_id = nullif(current_setting('anum.workspace_id', true)" in sql
    assert 'ondelete="RESTRICT"' in sql
    for table in (
        "agent_meeting_participants", "agent_meeting_turns", "agent_meeting_turn_leases"
    ):
        assert f'"{table}"' in sql
    for constraint in (
        "uq_agent_meeting_turn_sequence", "uq_agent_meeting_turn_request",
        "uq_agent_meeting_lease_sequence", "uq_agent_meeting_lease_request",
        "uq_agent_meeting_participant_actor",
    ):
        assert constraint in sql


def test_migration_has_no_drop_in_upgrade_or_cross_tenant_grants():
    sql = MIGRATION.read_text(encoding="utf-8")
    upgrade = sql.split("def upgrade() -> None:", 1)[1].split("def downgrade() -> None:", 1)[0]
    assert "drop_table" not in upgrade
    assert "grant all" not in upgrade.lower()
