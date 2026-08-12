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
