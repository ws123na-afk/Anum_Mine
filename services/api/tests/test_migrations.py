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


def test_control_plane_migration_puts_every_new_table_under_forced_rls() -> None:
    api_root = Path(__file__).parents[1]
    revision_text = (api_root / "migrations" / "versions" / "0008_control_plane_stores.py").read_text(
        encoding="utf-8"
    )

    assert 'revision = "0008_control_plane_stores"' in revision_text
    assert 'down_revision = "0007_event_outbox"' in revision_text
    assert "force row level security" in revision_text
    assert "create policy tenant_isolation_{table}" in revision_text
    for table in (
        "skill_versions",
        "skill_installations",
        "policy_packs",
        "role_templates",
        "approval_rules",
        "memory_governance",
        "marketplace_packages",
        "marketplace_installs",
        "routing_targets",
        "integration_configurations",
        "workspace_files",
        "notification_preferences",
    ):
        assert f'"{table}"' in revision_text
    # File bytes stay in object storage; only the key and digest are columns.
    assert '"storage_key"' in revision_text and '"content"' not in revision_text
    assert "bypassrls" not in revision_text.lower()


def test_model_budget_migration_extends_the_chain_with_forced_rls() -> None:
    api_root = Path(__file__).parents[1]
    revision_text = (api_root / "migrations" / "versions" / "0009_model_budgets.py").read_text(
        encoding="utf-8"
    )

    assert 'revision = "0009_model_budgets"' in revision_text
    assert 'down_revision = "0008_control_plane_stores"' in revision_text
    assert "force row level security" in revision_text
    assert "bypassrls" not in revision_text.lower()
    for table in ("model_budgets", "model_usage_monthly"):
        assert f'_policies("{table}"' in revision_text


def test_approval_integrity_migration_adds_binding_columns_without_touching_rls() -> None:
    api_root = Path(__file__).parents[1]
    revision_text = (api_root / "migrations" / "versions" / "0010_approval_integrity.py").read_text(
        encoding="utf-8"
    )

    assert 'revision = "0010_approval_integrity"' in revision_text
    assert 'down_revision = "0009_model_budgets"' in revision_text
    for column in ("run_id", "step_id", "arguments", "payload_hash", "expires_at", "decided_by"):
        assert f'"{column}"' in revision_text
        assert f'op.drop_column("approvals", "{column}")' in revision_text
    assert "payload_hash ~ '^[0-9a-f]{64}$'" in revision_text
    # The approvals table keeps its existing forced RLS policy unchanged.
    lowered = revision_text.lower()
    assert "create policy" not in lowered and "drop policy" not in lowered
    assert "disable row level security" not in lowered and "no force row level security" not in lowered
    assert "bypassrls" not in revision_text.lower()


def test_voice_and_automation_migration_uses_forced_rls_and_a_discovery_only_role() -> None:
    api_root = Path(__file__).parents[1]
    revision_text = (api_root / "migrations" / "versions" / "0011_voice_automation.py").read_text(
        encoding="utf-8"
    )

    assert 'revision = "0011_voice_automation"' in revision_text
    assert 'down_revision = "0010_approval_integrity"' in revision_text
    for table in (
        "voice_sessions",
        "voice_transcript_segments",
        "automation_workflows",
        "automation_schedules",
        "automation_runs",
    ):
        assert f'"{table}"' in revision_text
    assert "force row level security" in revision_text
    # Voice rows are private to their user, not only to the workspace.
    assert "current_setting('anum.user_id', true)" in revision_text
    # The maintenance role discovers work through column grants and its own policies;
    # it is never given write access or BYPASSRLS.
    assert "create role anum_maintenance nologin" in revision_text
    assert "for select to anum_maintenance" in revision_text
    assert "to anum_maintenance" in revision_text
    for verb in ("insert", "update", "delete"):
        assert f"grant {verb}" not in revision_text.lower()
        assert f"for {verb} to anum_maintenance" not in revision_text
    assert "bypassrls" not in revision_text.lower()
    assert "create function" not in revision_text.lower()  # no SECURITY DEFINER escape hatch
    # Scheduler double-run guard: one run per idempotency key.
    assert "uq_automation_runs_idempotency" in revision_text


def test_approval_policy_migration_extends_the_chain_with_forced_rls() -> None:
    api_root = Path(__file__).parents[1]
    revision_text = (api_root / "migrations" / "versions" / "0012_approval_policy.py").read_text(
        encoding="utf-8"
    )

    assert 'revision = "0012_approval_policy"' in revision_text
    assert 'down_revision = "0011_voice_automation"' in revision_text
    assert len("0012_approval_policy") <= 32
    for column in ("decision_reason", "requested_by", "target"):
        assert f'op.drop_column("approvals", "{column}")' in revision_text
    assert '"workspace_approval_policies"' in revision_text
    assert "alter table workspace_approval_policies force row level security" in revision_text
    assert "tenant_isolation_workspace_approval_policies" in revision_text
    assert "current_setting('anum.workspace_id', true)" in revision_text
    assert "bypassrls" not in revision_text.lower()
    assert "create function" not in revision_text.lower()


def test_approval_chain_and_directory_migration_uses_forced_rls_and_a_read_only_role() -> None:
    api_root = Path(__file__).parents[1]
    revision_text = (
        api_root / "migrations" / "versions" / "0013_approvers_and_directory.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "0013_approvers_and_directory"' in revision_text
    assert 'down_revision = "0012_approval_policy"' in revision_text
    assert len("0013_approvers_and_directory") <= 32
    assert '"approval_approvers"' in revision_text
    assert "alter table approval_approvers force row level security" in revision_text
    assert "tenant_isolation_approval_approvers" in revision_text
    assert "create role anum_membership_reader nologin" in revision_text
    assert "for select to anum_membership_reader" in revision_text
    assert "current_setting('anum.user_id', true)" in revision_text
    for verb in ("insert", "update", "delete", "all"):
        assert f"for {verb} to anum_membership_reader" not in revision_text
    assert "grant insert" not in revision_text.lower() and "grant update" not in revision_text.lower()
    assert "bypassrls" not in revision_text.lower()
    assert "security definer" not in revision_text.lower()
    assert 'op.drop_table("approval_approvers")' in revision_text
