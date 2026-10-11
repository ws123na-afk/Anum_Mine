from pathlib import Path

from anum_api.db import email_repository as _email_tables  # noqa: F401 - registers the email tables
from anum_api.db.models import Base


def test_tenant_scoped_tables_include_tenant_id() -> None:
    tenant_scoped_tables = {
        "conversations",
        "conversation_turns",
        "voice_sessions",
        "voice_transcript_segments",
        "automation_workflows",
        "automation_schedules",
        "automation_runs",
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
    "integration_health_checks",
    "integration_credentials",
        "workspace_files",
        "notification_preferences",
        "user_preferences",
        "email_accounts",
        "email_messages",
        "email_drafts",
        "email_oauth_requests",
        "learning_profiles",
        "learning_signals",
        "learning_preferences",
        "learning_routines",
        "learning_notes",
        "learning_suggestions",
        "audit_records",
        "workspace_invitations",
        "workspaces",
        "workspace_memberships",
        "workspace_model_configs",
        "tasks",
        "agent_runs",
        "agent_run_steps",
        "approvals",
        "domain_events",
    }

    for table_name in tenant_scoped_tables:
        table = Base.metadata.tables[table_name]
        assert "tenant_id" in table.columns
        assert table.columns["tenant_id"].nullable is False


def test_core_database_tables_are_declared() -> None:
    assert {
        "tenants",
        "workspaces",
        "workspace_memberships",
        "tasks",
        "agent_runs",
        "agent_run_steps",
        "approvals",
        "domain_events",
    }.issubset(Base.metadata.tables.keys())


def test_initial_migration_enables_rls_and_pgvector() -> None:
    migration = Path(__file__).parents[1] / "migrations" / "0001_foundation.sql"
    sql = migration.read_text(encoding="utf-8")

    assert "create extension if not exists vector" in sql
    assert "alter table tasks enable row level security" in sql
    assert "alter table tasks force row level security" in sql
    assert "create policy tenant_isolation_tasks" in sql
    assert "workspace_id = nullif(current_setting('anum.workspace_id', true), '')" in sql
