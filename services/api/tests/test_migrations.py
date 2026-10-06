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


def test_workspace_membership_migration_extends_the_chain_with_rls() -> None:
    api_root = Path(__file__).parents[1]
    revision = api_root / "migrations" / "versions" / "0003_workspace_memberships.py"
    revision_text = revision.read_text(encoding="utf-8")

    assert 'revision = "0003_workspace_memberships"' in revision_text
    assert 'down_revision = "0002_memory_retention"' in revision_text
    assert '"workspace_memberships"' in revision_text
    assert "tenant_isolation_workspace_memberships" in revision_text


def test_workspace_model_config_migration_extends_the_chain_with_rls() -> None:
    api_root = Path(__file__).parents[1]
    revision = api_root / "migrations" / "versions" / "0005_workspace_model_configs.py"
    revision_text = revision.read_text(encoding="utf-8")

    assert 'revision = "0005_workspace_model_configs"' in revision_text
    assert 'down_revision = "0004_run_checkpoints"' in revision_text
    assert '"api_key_ciphertext"' in revision_text
    assert '"api_key"' not in revision_text  # only ciphertext is stored
    assert "alter table workspace_model_configs force row level security" in revision_text
    assert "tenant_isolation_workspace_model_configs" in revision_text
    assert 'op.drop_table("workspace_model_configs")' in revision_text


def test_workspace_invitation_migration_adds_rls_tables_and_stores_only_token_hashes() -> None:
    api_root = Path(__file__).parents[1]
    revision_text = (api_root / "migrations" / "versions" / "0006_workspace_invitations.py").read_text(
        encoding="utf-8"
    )

    assert 'revision = "0006_workspace_invitations"' in revision_text
    assert 'down_revision = "0005_workspace_model_configs"' in revision_text
    assert '"token_hash"' in revision_text
    assert '"token"' not in revision_text  # the raw token is never a column
    assert "alter table workspace_invitations force row level security" in revision_text
    assert "alter table audit_records force row level security" in revision_text
    assert "tenant_isolation_workspace_invitations" in revision_text
    # Audit history is append-only: only select and insert policies exist.
    assert "for select using" in revision_text and "for insert with check" in revision_text
    assert "for update" not in revision_text and "for delete" not in revision_text


def test_event_outbox_migration_uses_a_narrow_relay_role() -> None:
    api_root = Path(__file__).parents[1]
    revision_text = (api_root / "migrations" / "versions" / "0007_event_outbox.py").read_text(
        encoding="utf-8"
    )

    assert 'revision = "0007_event_outbox"' in revision_text
    assert 'down_revision = "0006_workspace_invitations"' in revision_text
    assert "create role anum_outbox_relay nologin" in revision_text
    assert "for select to anum_outbox_relay" in revision_text
    assert "for update to anum_outbox_relay" in revision_text
    assert "bypassrls" not in revision_text.lower()
    assert "grant update (" in revision_text  # column-level, never the whole row
