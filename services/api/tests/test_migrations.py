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


def test_memory_sources_migration_is_expand_only_with_owner_scoped_restrictive_rls() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0021_memory_sources.py").read_text(encoding="utf-8")
    assert 'revision = "0021_memory_sources"' in revision_text
    assert len("0021_memory_sources") <= 32
    assert 'down_revision = "0020_tenants_rls"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0021_memory_sources.py"]
    assert not [path for path in others if 'down_revision = "0020_tenants_rls"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    # Two nullable owner columns: the previous release never writes them.
    assert upgrade.count("op.add_column(") == 2
    assert upgrade.count("nullable=true") == 2
    assert '"memories"' in upgrade and '"retrieval_sources"' in upgrade
    # Owned rows: AND-ed with tenant isolation, visible only to the owner's user context.
    assert "as restrictive" in upgrade and "with check" in upgrade
    assert "current_setting('anum.user_id', true)" in revision_text
    assert "create index concurrently if not exists ix_memories_owner" in upgrade
    assert "autocommit_block" in upgrade
    assert "bypassrls" not in upgrade and "security definer" not in upgrade
    assert "grant " not in upgrade
    assert "drop " not in upgrade and "alter_column" not in upgrade and "nullable=false" not in upgrade


def test_user_preferences_migration_is_expand_only_with_user_scoped_forced_rls() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0025_user_preferences.py").read_text(encoding="utf-8")
    assert 'revision = "0025_user_preferences"' in revision_text
    assert len("0025_user_preferences") <= 32
    assert 'down_revision = "0024_model_config_routes"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0025_user_preferences.py"]
    assert not [path for path in others if 'down_revision = "0024_model_config_routes"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    assert "op.create_table(" in upgrade and "tenants.id" in upgrade
    assert "enable row level security" in upgrade and "force row level security" in upgrade
    assert "using (" in upgrade and "with check (" in upgrade
    assert "current_setting('anum.tenant_id', true)" in revision_text
    assert "current_setting('anum.user_id', true)" in revision_text
    assert "server_default=sa.true()" in upgrade
    assert "bypassrls" not in upgrade and "security definer" not in upgrade and "grant " not in upgrade
    assert "drop " not in upgrade and "add_column" not in upgrade and "alter_column" not in upgrade


def test_task_archive_migration_is_expand_only() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0026_task_archive.py").read_text(encoding="utf-8")
    assert 'revision = "0026_task_archive"' in revision_text
    assert len("0026_task_archive") <= 32
    assert 'down_revision = "0025_user_preferences"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0026_task_archive.py"]
    assert not [path for path in others if 'down_revision = "0025_user_preferences"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    # Two nullable columns the previous release never writes, and a concurrent index.
    assert upgrade.count("op.add_column(") == 2
    assert upgrade.count("nullable=true") == 2
    assert "create index concurrently if not exists ix_agent_runs_task_created" in upgrade
    assert "autocommit_block" in upgrade
    assert "drop " not in upgrade and "alter_column" not in upgrade and "nullable=false" not in upgrade
    assert "bypassrls" not in upgrade and "security definer" not in upgrade and "grant " not in upgrade


def test_push_devices_migration_is_expand_only_with_user_scoped_forced_rls() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0027_push_devices.py").read_text(encoding="utf-8")
    assert 'revision = "0027_push_devices"' in revision_text
    assert len("0027_push_devices") <= 32
    assert 'down_revision = "0026_task_archive"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0027_push_devices.py"]
    assert not [path for path in others if 'down_revision = "0026_task_archive"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    assert "op.create_table(" in upgrade and "workspaces.tenant_id" in upgrade
    assert "enable row level security" in upgrade and "force row level security" in upgrade
    assert "using (" in upgrade and "with check (" in upgrade
    for setting in ("anum.tenant_id", "anum.workspace_id", "anum.user_id"):
        assert f"current_setting('{setting}', true)" in revision_text
    # Tokens are stored encrypted; only a digest and a four-character hint are in clear.
    assert "token_ciphertext" in upgrade and "token_sha256" in upgrade and "token_hint" in upgrade
    assert '"token",' not in upgrade
    # The new preference column has a default, so the previous release can still insert.
    assert "add_column(" in upgrade and "server_default=sa.true()" in upgrade
    # The discovery grant is column-limited and only for the narrow maintenance role.
    assert "grant select (id, tenant_id, workspace_id, status) on developer_requests to anum_maintenance" in upgrade
    assert "for select to anum_maintenance" in upgrade
    assert "bypassrls" not in upgrade and "security definer" not in upgrade
    assert "drop " not in upgrade and "alter_column" not in upgrade


def test_workspace_admin_migration_is_expand_only_and_concurrent() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0028_workspace_admin.py").read_text(encoding="utf-8")
    assert 'revision = "0028_workspace_admin"' in revision_text
    assert len("0028_workspace_admin") <= 32
    assert 'down_revision = "0027_push_devices"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0028_workspace_admin.py"]
    assert not [path for path in others if 'down_revision = "0027_push_devices"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    # Two nullable columns the previous release never writes.
    assert upgrade.count("op.add_column(") == 2 and upgrade.count("nullable=true") == 2
    # The directory role reads one more column, read-only; nothing else is granted.
    assert upgrade.count("grant ") == 1
    assert "grant select (archived_at) on workspaces to anum_membership_reader" in upgrade
    assert "autocommit_block" in upgrade and upgrade.count("create index concurrently if not exists") == 2
    assert "bypassrls" not in upgrade and "security definer" not in upgrade


def test_file_management_migration_is_expand_only() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0029_file_management.py").read_text(encoding="utf-8")
    assert 'revision = "0029_file_management"' in revision_text
    assert len("0029_file_management") <= 32
    assert 'down_revision = "0028_workspace_admin"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0029_file_management.py"]
    assert not [path for path in others if 'down_revision = "0028_workspace_admin"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    # Two nullable columns the previous release ignores, and indexes built without a write lock.
    assert upgrade.count("op.add_column(") == 2
    assert upgrade.count("nullable=true") == 2
    assert upgrade.count("create index concurrently if not exists") == 2
    assert "autocommit_block" in upgrade
    assert "bypassrls" not in upgrade and "security definer" not in upgrade and "grant " not in upgrade
    assert "drop " not in upgrade and "alter_column" not in upgrade and "nullable=false" not in upgrade


def test_automation_policy_migration_is_expand_only() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0031_automation_policy.py").read_text(encoding="utf-8")
    assert 'revision = "0031_automation_policy"' in revision_text
    assert len("0031_automation_policy") <= 32
    assert 'down_revision = "0030_integration_health"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0031_automation_policy.py"]
    assert not [path for path in others if 'down_revision = "0030_integration_health"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    # New columns are nullable or carry a server default, so the previous release keeps inserting.
    assert upgrade.count("op.add_column(") == 4
    assert "server_default=\"allow\"" in upgrade
    assert "create index concurrently if not exists ix_automation_runs_workflow" in upgrade and "autocommit_block" in upgrade
    assert "drop " not in upgrade and "alter_column" not in upgrade
    assert "bypassrls" not in upgrade and "security definer" not in upgrade and "grant " not in upgrade


def test_whatsapp_migration_is_expand_only_with_forced_rls_and_narrow_routing() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0033_whatsapp.py").read_text(encoding="utf-8")
    assert 'revision = "0033_whatsapp"' in revision_text and len("0033_whatsapp") <= 32
    assert 'down_revision = "0032_developer_projects"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0033_whatsapp.py"]
    assert not [path for path in others if 'down_revision = "0032_developer_projects"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    assert upgrade.count("op.create_table(") == 4
    assert "enable row level security" in upgrade and "force row level security" in upgrade
    assert "current_setting('anum.workspace_id', true)" in revision_text
    # Webhook routing: column grants to the discovery-only role, select only, no secrets.
    grant = upgrade.split("grant select (", 1)[1].split(")", 1)[0]
    assert grant == "tenant_id, workspace_id, phone_number_id, verify_token_hash"
    assert "for select to anum_maintenance" in upgrade
    assert "ciphertext" not in grant
    assert "bypassrls" not in upgrade and "security definer" not in upgrade


def test_autonomy_migration_is_expand_only_with_forced_rls_on_every_table() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0034_autonomy.py").read_text(encoding="utf-8")
    assert 'revision = "0034_autonomy"' in revision_text and len("0034_autonomy") <= 32
    assert 'down_revision = "0033_whatsapp"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0034_autonomy.py"]
    assert not [path for path in others if 'down_revision = "0033_whatsapp"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    for table in (
        "autonomy_guardrails", "workspace_autonomy_settings", "autonomy_rules", "autonomy_proposals",
        "autonomy_actions", "autonomy_recipients",
    ):
        assert f'"{table}"' in revision_text
    assert '_force_rls("autonomy_guardrails", tenant_predicate)' in upgrade
    assert "for table in workspace_tables:" in upgrade and "_force_rls(table, workspace_predicate)" in upgrade
    assert "force row level security" in revision_text and "with check (" in revision_text
    assert "current_setting('anum.workspace_id', true)" in revision_text
    assert "bypassrls" not in upgrade and "security definer" not in upgrade and "grant " not in upgrade


def test_email_inbox_migration_is_expand_only_with_forced_rls_and_narrow_discovery() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0035_email_inbox.py").read_text(encoding="utf-8")
    assert 'revision = "0035_email_inbox"' in revision_text
    assert len("0035_email_inbox") <= 32
    assert 'down_revision = "0034_autonomy"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0035_email_inbox.py"]
    assert not [path for path in others if 'down_revision = "0034_autonomy"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    assert upgrade.count("op.create_table(") == 4
    assert "enable row level security" in upgrade and "force row level security" in upgrade
    assert "using (" in upgrade and "with check (" in upgrade
    assert "current_setting('anum.tenant_id', true)" in revision_text
    assert "current_setting('anum.workspace_id', true)" in revision_text
    # The maintenance role sees ids and scope of connected accounts, never tokens or addresses.
    grant = next(line for line in upgrade.splitlines() if "grant select" in line)
    assert "token" not in grant and "address" not in grant and "anum_maintenance" in grant
    assert "for select to anum_maintenance" in upgrade
    assert "bypassrls" not in upgrade and "security definer" not in upgrade


def test_learning_migration_is_expand_only_with_user_scoped_forced_rls() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0036_learning.py").read_text(encoding="utf-8")
    assert 'revision = "0036_learning"' in revision_text
    assert len("0036_learning") <= 32
    assert 'down_revision = "0035_email_inbox"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0036_learning.py"]
    assert not [path for path in others if 'down_revision = "0035_email_inbox"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    assert upgrade.count("op.create_table(") == 6
    assert "enable row level security" in upgrade and "force row level security" in upgrade
    assert "using (" in upgrade and "with check (" in upgrade
    for setting in ("anum.tenant_id", "anum.workspace_id", "anum.user_id"):
        assert f"current_setting('{setting}', true)" in revision_text


def test_library_migration_is_expand_only_with_owner_scoped_forced_rls() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0042_library.py").read_text(encoding="utf-8")
    assert 'revision = "0042_library"' in revision_text
    assert len("0042_library") <= 32
    parent = revision_text.split('down_revision = "', 1)[1].split('"', 1)[0]
    assert parent.startswith("00") and len(parent) <= 32
    others = [path for path in versions.glob("*.py") if path.name != "0042_library.py"]
    assert not [path for path in others if f'down_revision = "{parent}"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    assert upgrade.count("op.create_table(") == 4
    assert "enable row level security" in upgrade and "force row level security" in upgrade
    # Favorites check the user; collections, items and reports a restrictive owner policy.
    assert "current_setting('anum.user_id', true)" in revision_text
    assert "as restrictive" in revision_text and "owner_user_id is null" in revision_text
    assert "bypassrls" not in upgrade and "security definer" not in upgrade and "grant " not in upgrade
    assert "drop " not in upgrade and "add_column" not in upgrade and "alter_column" not in upgrade


def test_tyre_planning_migration_is_expand_only_with_forced_rls_and_a_discovery_only_policy() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0037_tyre_planning.py").read_text(encoding="utf-8")
    assert 'revision = "0037_tyre_planning"' in revision_text
    assert len("0037_tyre_planning") <= 32
    assert 'down_revision = "0036_learning"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0037_tyre_planning.py"]
    assert not [path for path in others if 'down_revision = "0036_learning"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    for table in ("tyre_connections", "tyre_snapshots", "tyre_alerts", "tyre_reports"):
        assert f'"{table}"' in upgrade
    assert "enable row level security" in upgrade and "force row level security" in upgrade
    assert "using (" in upgrade and "with check (" in upgrade
    assert "current_setting('anum.tenant_id', true)" in revision_text
    assert "current_setting('anum.workspace_id', true)" in revision_text
    # Key rotation may discover ids of rows holding a ciphertext, nothing else.
    assert "grant select (tenant_id, workspace_id) on tyre_connections to anum_maintenance" in upgrade
    assert "using (secret_ciphertext is not null)" in upgrade
    assert "bypassrls" not in upgrade and "security definer" not in upgrade
    assert "drop " not in upgrade and "alter_column" not in upgrade
    assert "server_default=sa.true()" in upgrade  # the new notification column has a default


def test_commands_migration_is_expand_only_with_per_user_forced_rls() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0044_commands.py").read_text(encoding="utf-8")
    assert 'revision = "0044_commands"' in revision_text and len("0044_commands") <= 32
    assert 'down_revision = "0043_meetings"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0044_commands.py"]
    assert not [path for path in others if 'down_revision = "0043_meetings"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    assert upgrade.count("op.create_table(") == 2
    assert "enable row level security" in upgrade and "force row level security" in upgrade
    assert "using (" in upgrade and "with check (" in upgrade
def test_home_migration_is_expand_only_with_user_scoped_forced_rls() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0046_home.py").read_text(encoding="utf-8")
    assert 'revision = "0046_home"' in revision_text and len("0046_home") <= 32
    assert 'down_revision = "0050_rotation_retention"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0046_home.py"]
    assert not [path for path in others if 'down_revision = "0050_rotation_retention"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    assert upgrade.count("op.create_table(") == 3
    for table in ("home_profiles", "home_pins", "home_insight_states"):
        assert f'"{table}"' in revision_text
    assert '"home_commands"' not in revision_text  # quick commands live in the command module
    assert '_force_rls("home_profiles", user_predicate)' in upgrade
    assert "for table in workspace_tables:" in upgrade and "_force_rls(table, workspace_user_predicate)" in upgrade
    assert "force row level security" in revision_text and "with check (" in revision_text
    for setting in ("anum.tenant_id", "anum.workspace_id", "anum.user_id"):
        assert f"current_setting('{setting}', true)" in revision_text
    assert "bypassrls" not in upgrade and "security definer" not in upgrade and "grant " not in upgrade
    assert "drop " not in upgrade and "add_column" not in upgrade and "alter_column" not in upgrade


def test_team_work_migration_is_expand_only_with_forced_rls_and_narrow_roles() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0039_team_work.py").read_text(encoding="utf-8")
    assert 'revision = "0039_team_work"' in revision_text
    assert len("0039_team_work") <= 32
    assert 'down_revision = "0042_library"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0039_team_work.py"]
    assert not [path for path in others if 'down_revision = "0042_library"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    # Expand only: new tables, one nullable column, roles and policies.
    assert "drop " not in upgrade and "alter_column" not in upgrade
    assert 'op.add_column("tasks", sa.column("assistant_id", sa.string(80), nullable=true))' in upgrade
    assert "bypassrls" not in upgrade and "security definer" not in upgrade
    assert "force row level security" in revision_text and "_force(table)" in upgrade and '_force("user_notifications")' in upgrade
    for role in ("anum_platform_reader", "anum_tenant_directory"):
        assert f'_role("{role}")' in upgrade
        assert f"to {role}" in upgrade
    # The narrow roles get column grants only, never content columns.
    assert "grant select (tenant_id, status, due_at, updated_at) on {table}" not in upgrade
    assert "title" not in upgrade.split('_role("anum_platform_reader")', 1)[1].split('_role("anum_tenant_directory")', 1)[0]
    assert "for select to anum_maintenance" in upgrade
    bootstrap = (Path(__file__).parents[3] / "infra" / "helm" / "bootstrap-database.sql").read_text(encoding="utf-8")
    assert "grant anum_platform_reader to anum_app with inherit false, set true;" in bootstrap
    assert "grant anum_tenant_directory to anum_app with inherit false, set true;" in bootstrap


def test_reports_calendar_migration_is_expand_only_with_forced_rls() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0045_reports_calendar.py").read_text(encoding="utf-8")
    assert 'revision = "0045_reports_calendar"' in revision_text and len("0045_reports_calendar") <= 32
    assert 'down_revision = "0039_team_work"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0045_reports_calendar.py"]
    assert not [path for path in others if 'down_revision = "0039_team_work"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    assert upgrade.count("op.create_table(") == 1 and '"calendar_events"' in revision_text
    # Expand only: the two report columns have defaults, nothing is dropped or altered.
    assert upgrade.count("op.add_column(") == 2 and upgrade.count("server_default") >= 2
    assert '"tabs"' in upgrade and '"sections"' in upgrade
    assert "enable row level security" in upgrade and "force row level security" in upgrade
    assert "using (" in upgrade and "with check (" in upgrade
    assert "current_setting('anum.tenant_id', true)" in revision_text
    assert "current_setting('anum.workspace_id', true)" in revision_text
    assert "bypassrls" not in upgrade and "security definer" not in upgrade and "grant " not in upgrade
    assert "drop " not in upgrade and "alter_column" not in upgrade


def test_marketing_migration_is_expand_only_with_forced_rls_and_discovery_only_grants() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0040_marketing.py").read_text(encoding="utf-8")
    assert 'revision = "0040_marketing"' in revision_text and len("0040_marketing") <= 32
    assert 'down_revision = "0049_automation_triggers"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0040_marketing.py"]
    assert not [path for path in others if 'down_revision = "0049_automation_triggers"' in path.read_text(encoding="utf-8")]
    lowered = revision_text.lower()
    upgrade = lowered.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    for table in ("marketing_accounts", "marketing_campaigns", "marketing_posts", "marketing_preferences"):
        assert f'"{table}"' in lowered
    assert upgrade.count("op.create_table(") == 4
    assert "enable row level security" in lowered and "force row level security" in lowered
    assert "with check (" in lowered
    assert "current_setting('anum.tenant_id', true)" in lowered and "current_setting('anum.workspace_id', true)" in lowered
    # The maintenance role discovers due posts and token rows by id and scope only, never content.
    assert "grant select (id, tenant_id, workspace_id) on marketing_accounts to anum_maintenance" in upgrade
    assert "grant select (id, tenant_id, workspace_id, status, scheduled_at, next_attempt_at) " in upgrade
    assert "text" not in upgrade.split("grant select (id, tenant_id, workspace_id, status", 1)[1].split(")", 1)[0]
    assert "bypassrls" not in upgrade and "security definer" not in upgrade
    assert "drop " not in upgrade and "alter_column" not in upgrade and "add_column" not in upgrade


def test_chat_cards_migration_adds_one_nullable_column() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0047_chat_cards.py").read_text(encoding="utf-8")
    assert 'revision = "0047_chat_cards"' in revision_text
    assert len("0047_chat_cards") <= 32
    assert 'down_revision = "0040_marketing"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0047_chat_cards.py"]
    assert not [path for path in others if 'down_revision = "0040_marketing"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    # One nullable column the previous release never reads or writes.
    assert upgrade.count("op.add_column(") == 1 and "nullable=true" in upgrade
    assert "drop " not in upgrade and "alter_column" not in upgrade and "nullable=false" not in upgrade
    assert "bypassrls" not in upgrade and "security definer" not in upgrade and "grant " not in upgrade


def test_task_planning_migration_is_expand_only() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0048_task_planning.py").read_text(encoding="utf-8")
    assert 'revision = "0048_task_planning"' in revision_text and len("0048_task_planning") <= 32
    assert 'down_revision = "0047_chat_cards"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0048_task_planning.py"]
    assert not [path for path in others if 'down_revision = "0047_chat_cards"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    # Nullable columns only: the previous release keeps inserting tasks without them.
    assert upgrade.count("op.add_column(") == 2 and upgrade.count("nullable=true") == 2
    assert "drop" not in upgrade and "alter_column" not in upgrade
    assert "bypassrls" not in upgrade and "security definer" not in upgrade


def test_agent_profiles_migration_is_expand_only_with_forced_rls() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0041_agent_profiles.py").read_text(encoding="utf-8")
    assert 'revision = "0041_agent_profiles"' in revision_text
    assert len("0041_agent_profiles") <= 32
    assert 'down_revision = "0048_task_planning"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0041_agent_profiles.py"]
    assert not [path for path in others if 'down_revision = "0048_task_planning"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    assert upgrade.count("op.create_table(") == 2
    assert upgrade.count("_force_rls(") == 2
    assert "enable row level security" in revision_text and "force row level security" in revision_text
    assert "using (" in revision_text and "with check (" in revision_text
    assert "current_setting('anum.user_id', true)" in revision_text
    assert "sharing = 'workspace'" in revision_text
    # New columns are nullable and the index is built without blocking writes.
    assert 'op.add_column("tasks", sa.column("agent_id", sa.string(80), nullable=true))' in upgrade
    assert 'op.add_column("conversations", sa.column("agent_id", sa.string(80), nullable=true))' in upgrade
    assert "create index concurrently if not exists ix_tasks_scope_agent" in upgrade and "autocommit_block" in upgrade
    assert "bypassrls" not in upgrade and "security definer" not in upgrade and "grant " not in upgrade
    assert "drop " not in upgrade and "alter_column" not in upgrade


def test_rotation_retention_migration_is_expand_only_with_narrow_discovery() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0050_rotation_retention.py").read_text(encoding="utf-8")
    assert 'revision = "0050_rotation_retention"' in revision_text
    assert len("0050_rotation_retention") <= 32
    assert 'down_revision = "0041_agent_profiles"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0050_rotation_retention.py"]
    assert not [path for path in others if 'down_revision = "0041_agent_profiles"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    # One nullable column, column grants of ids, scope and age only, five select-only policies.
    assert "nullable=true" in upgrade and "drop " not in upgrade and "alter_column" not in upgrade
    grants = [line for line in upgrade.splitlines() if "grant select" in line]
    assert [line for line in grants if "(tenant_id, workspace_id, created_at) on whatsapp_conversations" in line]
    assert [line for line in grants if "(id, tenant_id, workspace_id) on commerce_customers" in line]
    assert all("anum_maintenance" in line and "ciphertext" not in line and "name" not in line for line in grants)
    assert upgrade.count("for select to anum_maintenance") == 5
    assert "bypassrls" not in upgrade and "security definer" not in upgrade


def test_chat_depth_migration_is_expand_only_and_grants_nothing() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0051_chat_depth.py").read_text(encoding="utf-8")
    assert 'revision = "0051_chat_depth"' in revision_text and len("0051_chat_depth") <= 32
    assert 'down_revision = "0046_home"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0051_chat_depth.py"]
    assert not [path for path in others if 'down_revision = "0046_home"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    # Three nullable columns and a check that accepts null: the previous release keeps working.
    assert upgrade.count("op.add_column(") == 3 and upgrade.count("nullable=true") == 3
    assert "feedback is null or feedback in ('up', 'down')" in upgrade
    assert "drop" not in upgrade and "alter_column" not in upgrade and "grant " not in upgrade
    assert "bypassrls" not in upgrade and "security definer" not in upgrade


def test_setup_plans_migration_is_expand_only_with_forced_rls() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0052_setup_plans.py").read_text(encoding="utf-8")
    assert 'revision = "0052_setup_plans"' in revision_text and len("0052_setup_plans") <= 32
    assert 'down_revision = "0051_chat_depth"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0052_setup_plans.py"]
    assert not [path for path in others if 'down_revision = "0051_chat_depth"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    assert upgrade.count("op.create_table(") == 2 and "drop" not in upgrade and "alter_column" not in upgrade
    assert "force row level security" in upgrade and "create policy scope_isolation_setup_plan_items" in revision_text
    assert "grant " not in upgrade and "bypassrls" not in upgrade and "security definer" not in upgrade


def test_routing_suppression_migration_is_expand_only_with_forced_rls() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0054_routing_suppression.py").read_text(encoding="utf-8")
    assert 'revision = "0054_routing_suppression"' in revision_text and len("0054_routing_suppression") <= 32
    assert 'down_revision = "0053_tyre_supabase_auth"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0054_routing_suppression.py"]
    assert not [path for path in others if 'down_revision = "0053_tyre_supabase_auth"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    # Defaulted or nullable columns on approval_rules and one new table: the previous release keeps working.
    assert upgrade.count("op.add_column(") == 4 and upgrade.count("op.create_table(") == 1
    assert upgrade.count("server_default") >= 2 and upgrade.count("nullable=true") == 2
    assert "drop" not in upgrade and "alter_column" not in upgrade
    assert "force row level security" in upgrade and "create policy tenant_isolation_contact_suppressions" in revision_text
    assert "grant " not in upgrade and "bypassrls" not in upgrade and "security definer" not in upgrade


def test_plain_dashes_migration_is_data_only_and_keeps_rls_forced() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0055_plain_dashes.py").read_text(encoding="utf-8")
    assert 'revision = "0055_plain_dashes"' in revision_text and len("0055_plain_dashes") <= 32
    assert 'down_revision = "0054_routing_suppression"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0055_plain_dashes.py"]
    assert not [path for path in others if 'down_revision = "0054_routing_suppression"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    # No schema change: the only DDL is the discovery policy, dropped in the same transaction.
    assert "op.add_column(" not in upgrade and "op.create_table(" not in upgrade and "alter table" not in upgrade
    assert upgrade.count("create policy {policy}") == 1 and upgrade.count("drop policy {policy}") == 1
    assert "for select to current_user" in upgrade
    assert "grant " not in upgrade and "bypassrls" not in upgrade and "security definer" not in upgrade
    assert "audit_records" not in revision_text.split("TABLES:", 1)[1].split("POLICY", 1)[0]


def test_finance_ledger_migration_is_expand_only_with_owner_only_forced_rls() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0056_finance_ledger.py").read_text(encoding="utf-8")
    assert 'revision = "0056_finance_ledger"' in revision_text and len("0056_finance_ledger") <= 32
    assert 'down_revision = "0055_plain_dashes"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0056_finance_ledger.py"]
    assert not [path for path in others if 'down_revision = "0055_plain_dashes"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    tables = (
        "finance_settings", "finance_accounts", "finance_imports", "finance_transactions",
        "finance_bills", "finance_debts", "finance_goals", "finance_holdings",
    )
    assert upgrade.count("op.create_table(") == len(tables)
    for table in tables:
        assert f'"{table}"' in revision_text
        assert f"_workspace_fk(\"{table}\")" in revision_text
    # Forced RLS, the tenant policy and a restrictive owner policy on every table (loop).
    assert "force row level security" in upgrade and "create policy tenant_isolation_{table}" in upgrade
    assert "create policy owner_only_{table}" in upgrade and "as restrictive" in upgrade
    assert "owner_user_id = nullif(current_setting('anum.user_id', true), '')" in revision_text
    # Exact money, a currency per amount, at most four digits of a number, dedupe per account.
    assert "MONEY = sa.Numeric(20, 4)" in revision_text and "last4 ~ '^[0-9]{4}$'" in upgrade
    assert '"account_id", "dedupe_hash"' in revision_text
    assert "drop" not in upgrade and "alter_column" not in upgrade and "add_column" not in upgrade
    assert "grant " not in upgrade and "bypassrls" not in upgrade and "security definer" not in upgrade


def test_finance_stages_migration_is_expand_only_read_only_and_owner_scoped() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0057_finance_plan_guard_conn.py").read_text(encoding="utf-8")
    assert 'revision = "0057_finance_plan_guard_conn"' in revision_text and len("0057_finance_plan_guard_conn") <= 32
    assert 'down_revision = "0056_finance_ledger"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0057_finance_plan_guard_conn.py"]
    assert not [path for path in others if 'down_revision = "0056_finance_ledger"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    tables = ("finance_plans", "finance_guardian_versions", "finance_guardian_changes", "finance_connections", "finance_fx_rates")
    assert upgrade.count("op.create_table(") == len(tables)
    for table in tables:
        assert f'"{table}"' in revision_text and f'_workspace_fk("{table}")' in revision_text
    assert "force row level security" in upgrade and "create policy tenant_isolation_{table}" in upgrade
    assert "create policy owner_only_{table}" in upgrade and "as restrictive" in upgrade
    # Expand only: a nullable column and new tables; nothing dropped or altered.
    assert 'op.add_column("finance_goals", sa.column("monthly_contribution", money, nullable=true))' in upgrade
    assert "drop" not in upgrade and "alter_column" not in upgrade
    # Read only in the database, versions unique per person, agents never loosen.
    assert "ck_finance_connections_read_only" in upgrade and 'like_regex \\"[:]read$\\"' in upgrade
    assert "uq_finance_guardian_versions_version" in upgrade and "ck_finance_guardian_changes_agent_strict" in upgrade
    # The maintenance role reads only discovery columns of due rows; nothing else is granted.
    grants = [line.strip() for line in upgrade.splitlines() if "grant " in line]
    assert grants == ['"grant select (id, tenant_id, workspace_id, owner_user_id, enabled, status, next_sync_at) "']
    assert "for select to anum_maintenance" in upgrade and "bypassrls" not in upgrade and "security definer" not in upgrade
    assert "current_user = 'anum_maintenance'" in revision_text


def test_finance_trading_migration_is_expand_only_owner_scoped_and_refuses_live() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0058_finance_paper_exec.py").read_text(encoding="utf-8")
    assert 'revision = "0058_finance_paper_exec"' in revision_text and len("0058_finance_paper_exec") <= 32
    assert 'down_revision = "0057_finance_plan_guard_conn"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0058_finance_paper_exec.py"]
    assert not [path for path in others if 'down_revision = "0057_finance_plan_guard_conn"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    owned = ("finance_prices", "finance_paper_portfolios", "finance_paper_orders", "finance_exec_intents", "finance_exec_settings")
    assert upgrade.count("op.create_table(") == len(owned) + 1
    for table in owned:
        assert f'"{table}"' in revision_text and f'_workspace_fk("{table}")' in revision_text
    # Forced RLS with the tenant policy and a restrictive owner policy on every per-person table;
    # the organisation's kill switch is tenant scoped.
    assert "force row level security" in upgrade and "create policy tenant_isolation_{table}" in upgrade
    assert "create policy owner_only_{table}" in upgrade and "as restrictive" in upgrade
    assert "alter table finance_exec_global force row level security" in upgrade
    assert '["tenant_id"], ["tenants.id"]' in revision_text
    # Expand only, nothing granted, nothing bypassed.
    assert "drop" not in upgrade and "alter_column" not in upgrade and "add_column" not in upgrade
    assert "grant " not in upgrade and "bypassrls" not in upgrade and "security definer" not in upgrade
    # The gateway's guarantees, in the database: one order per intent, only the owner approves,
    # nothing runs unapproved, agents only simulate, and a live request never runs.
    for constraint in (
        "uq_finance_paper_orders_intent", "uq_finance_exec_intents_key", "ck_finance_exec_intents_approver",
        "ck_finance_exec_intents_approved", "ck_finance_exec_intents_agent", "ck_finance_exec_intents_no_live",
    ):
        assert constraint in upgrade
    assert "approved_by is null or approved_by = owner_user_id" in upgrade
    assert "mode <> 'live' or (status in ('pending_approval', 'denied', 'refused', 'expired', 'rejected') and approved_by is null)" in upgrade


def test_finance_wider_live_migration_is_expand_only_and_one_tier_at_a_time() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0059_finance_wider_live.py").read_text(encoding="utf-8")
    assert 'revision = "0059_finance_wider_live"' in revision_text and len("0059_finance_wider_live") <= 32
    assert 'down_revision = "0058_finance_paper_exec"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0059_finance_wider_live.py"]
    assert not [path for path in others if 'down_revision = "0058_finance_paper_exec"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    assert upgrade.count("op.create_table(") == 2
    for table in ("finance_live_widenings", "finance_live_incidents"):
        assert f'"{table}"' in revision_text and f'_workspace_fk("{table}")' in revision_text
    assert "force row level security" in upgrade and "create policy owner_only_{table}" in upgrade and "as restrictive" in upgrade
    # Expand only: one column with a default and new tables.
    assert upgrade.count("op.add_column(") == 1 and "server_default=sa.text(\"'{}'::jsonb\")" in upgrade
    assert "drop" not in upgrade and "alter_column" not in upgrade
    assert "grant " not in upgrade and "bypassrls" not in upgrade and "security definer" not in upgrade
    assert "tier = base_tier + 1" in upgrade and "decided_by is null or decided_by = owner_user_id" in upgrade
    assert "tier_after <= tier_before" in upgrade


def test_finance_intel_migration_is_expand_only_owner_only_and_never_live() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0065_finance_intel.py").read_text(encoding="utf-8")
    assert 'revision = "0065_finance_intel"' in revision_text and len("0065_finance_intel") <= 32
    assert 'down_revision = "0064_mail_operator_view"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0065_finance_intel.py"]
    assert not [path for path in others if 'down_revision = "0064_mail_operator_view"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    tables = ("finance_instruments", "finance_market_bars", "finance_strategies", "finance_strategy_runs", "finance_agent_decisions")
    assert upgrade.count("op.create_table(") == len(tables)
    for table in tables:
        assert f'"{table}"' in revision_text and f'_workspace_fk("{table}")' in revision_text
    assert "force row level security" in upgrade and "create policy owner_only_{table}" in upgrade and "as restrictive" in upgrade
    assert "op.add_column(" not in upgrade and "drop" not in upgrade and "alter_column" not in upgrade
    assert "grant " not in upgrade and "bypassrls" not in upgrade and "security definer" not in upgrade
    # Six lifecycle states and no live one; only the person promotes or retires.
    assert "live" not in revision_text.split("STATES = ", 1)[1].split("\n", 1)[0].lower()
    assert "state not in ('paper_active', 'retired') or state_changed_by = 'person'" in upgrade
    assert "state <> 'paper_active' or portfolio_id is not null" in upgrade


def test_mail_operator_view_migration_is_expand_only_and_reads_counts_only() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0064_mail_operator_view.py").read_text(encoding="utf-8")
    assert 'revision = "0064_mail_operator_view"' in revision_text and len("0064_mail_operator_view") <= 32
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    assert upgrade.count("op.create_table(") == 1 and "force row level security" in upgrade
    # The platform reader gets counted columns only, never the address, password or error text.
    assert "grant select (tenant_id, status, last_checked_at) on mail_accounts to anum_platform_reader" in upgrade
    assert "grant select (tenant_id, day, metric, count) on mail_ops_counters to anum_platform_reader" in upgrade
    for column in ("address", "password_enc", "display_name", "last_error", "owner_user_id"):
        assert f"{column})" not in upgrade and f"{column}," not in upgrade
    # Expand only, no widening of the app role.
    assert "drop " not in upgrade and "alter_column" not in upgrade
    assert "bypassrls" not in upgrade and "security definer" not in upgrade
    assert "_extend_restrictive(add=true)" in upgrade and "or current_user = %l" in revision_text.lower()


def test_calls_migration_is_expand_only_with_forced_rls_and_narrow_discovery() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0071_calls.py").read_text(encoding="utf-8")
    assert 'revision = "0071_calls"' in revision_text and len("0071_calls") <= 32
    assert 'down_revision = "0070_business"' in revision_text
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    tables = ("call_contacts", "call_log", "call_opt_outs", "call_provider_accounts", "call_ai_requests")
    assert upgrade.count("op.create_table(") == len(tables)
    for table in tables:
        assert f'_workspace_fk("{table}")' in revision_text
    assert "force row level security" in revision_text and "for table in tables:\n        _enable_rls(table)" in upgrade
    assert "op.add_column(" not in upgrade and "drop" not in upgrade and "alter_column" not in upgrade
    assert "bypassrls" not in upgrade and "security definer" not in upgrade
    # Secrets only as ciphertext; anum_maintenance sees scope, route and recording expiry only.
    assert '"credentials_ciphertext"' in revision_text and 'sa.Column("auth_token"' not in revision_text
    assert "grant select (tenant_id, workspace_id, route_id) on call_provider_accounts to anum_maintenance" in upgrade
    assert "grant select (id, tenant_id, workspace_id, recording_expires_at) on call_log to anum_maintenance" in upgrade
    assert upgrade.count("grant ") == 2 and "insert" not in upgrade.split("grant ", 1)[1]
def test_business_migration_is_expand_only_with_forced_rls_on_every_table_and_scoped_references() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0070_business.py").read_text(encoding="utf-8")
    assert 'revision = "0070_business"' in revision_text and len("0070_business") <= 32
    down = revision_text.split('down_revision = "', 1)[1].split('"', 1)[0]
    others = [path for path in versions.glob("*.py") if path.name != "0070_business.py"]
    # Chained onto an existing revision that nothing else extends (one head).
    assert any(f'revision = "{down}"' in path.read_text(encoding="utf-8") for path in others)
    assert not [path for path in others if f'down_revision = "{down}"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    tables = revision_text.split("TABLES = (", 1)[1].split(")", 1)[0]
    names = [name.strip().strip('"') for name in tables.split(",") if name.strip()]
    assert len(names) == 16 and all(name.startswith("business_") for name in names)
    assert upgrade.count("op.create_table(") == len(names)
    assert "for table in tables:\n        _enable_rls(table)" in upgrade
    assert "force row level security" in revision_text.lower() and "with check (" in revision_text.lower()
    assert "current_setting('anum.tenant_id', true)" in revision_text and "current_setting('anum.workspace_id', true)" in revision_text
    # References carry the scope, so a row cannot point into another tenant or workspace.
    assert '["tenant_id", "workspace_id", column]' in revision_text
    assert 'f"{target}.tenant_id", f"{target}.workspace_id", f"{target}.id"' in revision_text
    # A locked (sent or accepted) proposal version cannot be updated; the trigger runs as the invoker.
    assert "when (old.locked)" in upgrade and "security invoker" in upgrade
    assert "grant " not in upgrade and "bypassrls" not in upgrade and "security definer" not in upgrade
    assert "op.add_column(" not in upgrade and "alter_column" not in upgrade and "drop " not in upgrade


def test_mail_rules_retention_migration_is_expand_only_and_discovery_reads_no_content() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0068_mail_rules_retention.py").read_text(encoding="utf-8")
    assert 'revision = "0068_mail_rules_retention"' in revision_text and len("0068_mail_rules_retention") <= 32
    assert 'down_revision = "0067_mail_sharing_contacts"' in revision_text
    others = [path for path in versions.glob("*.py") if path.name != "0068_mail_rules_retention.py"]
    assert not [path for path in others if 'down_revision = "0067_mail_sharing_contacts"' in path.read_text(encoding="utf-8")]
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    assert upgrade.count("op.create_table(") == 4 and "force row level security" in upgrade
    assert "op.add_column(" not in upgrade and "alter_column" not in upgrade and "drop " not in upgrade
    assert "bypassrls" not in upgrade and "security definer" not in upgrade
    # An active rule always carries the hash that was approved.
    assert "status <> 'active' or (approved_hash is not null and approved_hash = rule_hash)" in upgrade
    # The maintenance role gets scheduling and retention columns only, never content.
    import re

    columns = {column.replace("\"", "").strip() for found in re.findall(r"grant select \(([^)]*)\)", upgrade) for column in found.split(",")}
    assert columns == {
        "status", "account_id", "tenant_id", "workspace_id", "owner_user_id", "background_sync", "sent_retention_days",
        "draft_retention_days", "label_retention_days", "next_sync_at", "lease_until",
    }


def test_mail_writers_migration_is_expand_only() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    revision_text = (versions / "0069_mail_writers_uidvalidity.py").read_text(encoding="utf-8")
    assert 'revision = "0069_mail_writers_uidvalidity"' in revision_text and len("0069_mail_writers_uidvalidity") <= 32
    assert 'down_revision = "0068_mail_rules_retention"' in revision_text
    upgrade = revision_text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    assert "op.create_table(" not in upgrade and "drop " not in upgrade and "alter_column" not in upgrade
    assert "bypassrls" not in upgrade and "security definer" not in upgrade
    assert "grant select (disconnecting_at) on mail_accounts to anum_maintenance" in upgrade
    assert "grant select (updated_at) on mail_drafts to anum_maintenance" in upgrade
