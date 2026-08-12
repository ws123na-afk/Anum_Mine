from pathlib import Path


def test_alembic_baseline_references_foundation_sql() -> None:
    api_root = Path(__file__).parents[1]
    revision = api_root / "migrations" / "versions" / "0001_foundation.py"
    foundation_sql = api_root / "migrations" / "0001_foundation.sql"

    revision_text = revision.read_text(encoding="utf-8")
    sql_text = foundation_sql.read_text(encoding="utf-8")

    assert 'revision = "0001_foundation"' in revision_text
    assert '"0001_foundation.sql"' in revision_text
    assert "constraint fk_agent_runs_task foreign key (tenant_id, workspace_id, task_id)" in sql_text
    assert "alter table tasks enable row level security" in sql_text


def test_memory_retention_migration_extends_the_foundation_chain() -> None:
    api_root = Path(__file__).parents[1]
    revision = api_root / "migrations" / "versions" / "0002_memory_retention.py"
    revision_text = revision.read_text(encoding="utf-8")

    assert 'revision = "0002_memory_retention"' in revision_text
    assert 'down_revision = "0001_foundation"' in revision_text
    assert '"retention_expires_at"' in revision_text
    assert "sa.DateTime(timezone=True)" in revision_text
    assert 'op.drop_column("memories", "retention_expires_at")' in revision_text
