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


def test_retrieval_index_migration_extends_the_chain_with_forced_rls_and_pgvector() -> None:
    api_root = Path(__file__).parents[1]
    versions = api_root / "migrations" / "versions"
    revision_text = (versions / "0014_retrieval_index.py").read_text(encoding="utf-8")

    assert 'revision = "0014_retrieval_index"' in revision_text
    assert 'down_revision = "0013_approvers_and_directory"' in revision_text
    assert len("0014_retrieval_index") <= 32
    # The chain stays linear: no other revision builds on 0013.
    others = [path for path in versions.glob("*.py") if path.name != "0014_retrieval_index.py"]
    assert not [path for path in others if 'down_revision = "0013_approvers_and_directory"' in path.read_text(encoding="utf-8")]
    for table in ("retrieval_sources", "retrieval_chunks"):
        assert f'"{table}"' in revision_text
    assert "alter table {table} force row level security" in revision_text
    assert "create policy tenant_isolation_{table}" in revision_text
    assert "add column embedding vector not null" in revision_text
    assert "vector_dims(embedding) = dimensions" in revision_text
    assert 'ondelete="CASCADE"' in revision_text
    assert "bypassrls" not in revision_text.lower()
    assert "security definer" not in revision_text.lower()
    # Expand only: nothing existing is altered or dropped on upgrade.
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert "drop_" not in upgrade and "alter_column" not in upgrade
    assert 'op.drop_table("retrieval_chunks")' in revision_text


def test_retrieval_retention_migration_adds_a_discovery_only_policy_and_invoker_triggers() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0015_retrieval_retention.py").read_text(encoding="utf-8")

    assert 'revision = "0015_retrieval_retention"' in revision_text
    assert 'down_revision = "0014_retrieval_index"' in revision_text
    assert len("0015_retrieval_retention") <= 32
    others = [path for path in versions.glob("*.py") if path.name != "0015_retrieval_retention.py"]
    assert not [path for path in others if 'down_revision = "0014_retrieval_index"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    # Discovery only: column-level select for the maintenance role, a select-only policy.
    assert "grant select (tenant_id, workspace_id, source_type, source_id, source_expires_at)" in upgrade
    assert "for select to anum_maintenance" in upgrade
    assert "grant insert" not in upgrade and "grant update" not in upgrade and "grant delete" not in upgrade
    assert "on retrieval_chunks to" not in upgrade
    # The triggers' functions run as the invoker, under the same RLS as the delete.
    assert upgrade.count("security invoker") == 2
    assert "security definer" not in upgrade and "bypassrls" not in upgrade
    # Expand only.
    assert "drop " not in upgrade and "alter_column" not in upgrade and "alter table" not in upgrade


def test_hnsw_migration_indexes_exactly_the_dimensions_search_uses() -> None:
    from anum_api.db.retrieval_repository import ANN_DIMENSIONS

    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0016_retrieval_hnsw.py").read_text(encoding="utf-8")
    assert 'revision = "0016_retrieval_hnsw"' in revision_text
    assert 'down_revision = "0015_retrieval_retention"' in revision_text
    assert len("0016_retrieval_hnsw") <= 32
    others = [path for path in versions.glob("*.py") if path.name != "0016_retrieval_hnsw.py"]
    assert not [path for path in others if 'down_revision = "0015_retrieval_retention"' in path.read_text(encoding="utf-8")]
    declared = revision_text.split("ANN_DIMENSIONS = (", 1)[1].split(")", 1)[0]
    assert tuple(int(value) for value in declared.split(",")) == tuple(sorted(ANN_DIMENSIONS))
    # HNSW on pgvector's `vector` type supports at most 2,000 dimensions.
    assert max(ANN_DIMENSIONS) <= 2000
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert "create index concurrently if not exists" in upgrade and "using hnsw" in upgrade
    assert "where dimensions = {dims}" in upgrade and "vector_cosine_ops" in upgrade
    assert "drop " not in upgrade and "alter " not in upgrade


def test_tasks_scope_created_migration_is_concurrent_and_expand_only() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0017_tasks_scope_created.py").read_text(encoding="utf-8")
    assert 'revision = "0017_tasks_scope_created"' in revision_text
    assert 'down_revision = "0016_retrieval_hnsw"' in revision_text
    assert len("0017_tasks_scope_created") <= 32
    others = [path for path in versions.glob("*.py") if path.name != "0017_tasks_scope_created.py"]
    assert not [path for path in others if 'down_revision = "0016_retrieval_hnsw"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert "autocommit_block" in upgrade
    assert "create index concurrently if not exists ix_tasks_scope_created" in upgrade
    assert "on tasks (tenant_id, workspace_id, created_at desc, id desc)" in upgrade
    assert "drop " not in upgrade and "alter " not in upgrade


def test_conversations_migration_is_expand_only_with_user_scoped_forced_rls() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0018_conversations.py").read_text(encoding="utf-8")
    assert 'revision = "0018_conversations"' in revision_text
    assert len("0018_conversations") <= 32
    assert 'down_revision = "0017_tasks_scope_created"' in revision_text
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    for table in ("conversations", "conversation_turns"):
        assert f'"{table}"' in upgrade
    assert "force row level security" in upgrade
    assert "current_setting('anum.user_id', true)" in revision_text
    assert "{user_predicate}" in upgrade
    assert "bypassrls" not in upgrade and "security definer" not in upgrade
    assert "drop " not in upgrade and "alter_column" not in upgrade
    # No other role is granted anything: nothing discovers conversations across tenants.
    assert "grant " not in upgrade


def test_conversation_retention_migration_adds_a_discovery_only_policy() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0019_conversation_retention.py").read_text(encoding="utf-8")
    assert 'revision = "0019_conversation_retention"' in revision_text
    assert len("0019_conversation_retention") <= 32
    assert 'down_revision = "0018_conversations"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0019_conversation_retention.py"]
    assert not [path for path in others if 'down_revision = "0018_conversations"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    # Discovery only: ids, scope and the activity time; never the title or any turn.
    assert "grant select (id, tenant_id, workspace_id, user_id, updated_at) on conversations to anum_maintenance" in upgrade
    assert "for select to anum_maintenance" in upgrade
    assert "updated_at <= now() - interval '1 day'" in upgrade
    assert "conversation_turns" not in upgrade and "title" not in upgrade
    assert "grant insert" not in upgrade and "grant update" not in upgrade and "grant delete" not in upgrade
    assert "security definer" not in upgrade and "bypassrls" not in upgrade
    assert "drop " not in upgrade and "alter " not in upgrade


def test_tenants_rls_migration_forces_rls_on_tenants_and_grants_nothing() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0020_tenants_rls.py").read_text(encoding="utf-8")
    assert 'revision = "0020_tenants_rls"' in revision_text
    assert len("0020_tenants_rls") <= 32
    assert 'down_revision = "0019_conversation_retention"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0020_tenants_rls.py"]
    assert not [path for path in others if 'down_revision = "0019_conversation_retention"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    assert "alter table tenants enable row level security" in upgrade
    assert "alter table tenants force row level security" in upgrade
    assert "create policy tenant_isolation_tenants on tenants" in upgrade
    assert "using ({tenant_predicate})" in upgrade and "with check ({tenant_predicate})" in upgrade
    assert "id = nullif(current_setting('anum.tenant_id', true), '')" in revision_text
    # Expand only, and no role gets a way around the policy.
    assert "drop " not in upgrade and "add_column" not in upgrade and "alter_column" not in upgrade
    assert "grant " not in upgrade and "bypassrls" not in upgrade and "security definer" not in upgrade
