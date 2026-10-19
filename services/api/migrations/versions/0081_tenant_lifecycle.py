"""Organisation export and deletion (docs/organisation-lifecycle.md).

* ``tenant_lifecycle_jobs``: one row per organisation export, organisation deletion or
  workspace deletion, under forced RLS on ``anum.tenant_id`` (organisation wide, so the
  organisation's administrator sees every job; the API checks who may act). Partial
  unique indexes keep one open export per organisation, one open organisation deletion
  and one open deletion per workspace.
* ``anum_maintenance`` finds jobs that need work (ids, scope, status and times only) and,
  only while an export of that organisation is running, the ids of its workspaces.
* ``anum_tenant_purger``: a NOLOGIN role (no BYPASSRLS, no SECURITY DEFINER) holding
  ``DELETE`` and ``SELECT`` on the scope columns of every tenant table, with one policy
  per table (``lifecycle_purge_<table>``) that admits a row only when its tenant is the
  session's ``anum.tenant_id`` and that tenant has a job in ``purging`` covering the row.
  Every restrictive policy on those tables also admits the purger (as 0057 did for
  ``anum_maintenance``); the permissive purge policy still limits it to the purging
  tenant. The API reaches the role with ``SET LOCAL ROLE`` (granted ``WITH INHERIT FALSE,
  SET TRUE``).

* ``deleted_organizations``: the receipt the platform console lists after an organisation
  is purged (ids, dates, counts and the Keycloak cleanup outcome), under forced RLS on
  ``anum.tenant_id``; ``anum_platform_reader`` reads the operator's own rows and
  ``anum_maintenance`` finds receipts whose Keycloak cleanup is pending. Never purged.

Expand only: two new tables, new policies, grants to two NOLOGIN roles and a widened
restrictive predicate that admits only the purger. The previous release ignores them.

Numbered 0081 and chained after ``0076_business_support``, the head of ``main``; the owner
re-chains it at merge if another migration lands first.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0081_tenant_lifecycle"
down_revision = "0076_business_support"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
JOBS = "tenant_lifecycle_jobs"
DELETED = "deleted_organizations"
DELETED_COLUMNS = (
    "tenant_id", "job_id", "operator_user_id", "display_name", "plan", "requested_by", "requested_at",
    "purge_started_at", "deleted_at", "status", "row_count", "file_count", "counts", "identity_status",
    "identity_removed", "identity_detached", "identity_finished_at",
)
PURGER = "anum_tenant_purger"
KINDS = ("organisation_export", "organisation_deletion", "workspace_deletion")
STATUSES = ("queued", "running", "ready", "failed", "expired", "scheduled", "cancelled", "purging", "completed")
TENANT = "nullif(current_setting('anum.tenant_id', true), '')"

# Every table with a tenant_id column at this revision (anum_api/tenant_lifecycle_tables.py
# keeps the live list; a table added later brings its own policy, see _purge_policy).
# None: organisation wide; "id": the workspaces table itself.
TENANT_WIDE = (
    "approval_rules", "autonomy_guardrails", "contact_suppressions", "finance_exec_global", "home_profiles",
    "marketplace_packages", "memory_governance", "platform_organizations", "policy_packs", "role_templates",
    "routing_targets", "setup_plan_checks", "skill_versions", "tenant_branding", "user_preferences",
)
WORKSPACE_TABLES = (
    "agent_memories", "agent_profiles", "agent_run_steps", "agent_runs", "app_call_signals", "app_calls",
    "approval_approvers", "approvals", "assistant_profiles", "audit_records", "automation_runs",
    "automation_schedules", "automation_triggers", "automation_workflows", "autonomy_actions", "autonomy_proposals",
    "autonomy_recipients", "autonomy_rules", "brevo_campaign_drafts", "brevo_connections", "business_accounts",
    "business_activities", "business_contacts", "business_drafts", "business_evidence", "business_inferences",
    "business_leads", "business_milestones", "business_offers", "business_opportunities", "business_products",
    "business_projects", "business_proposal_versions", "business_proposals", "business_research_runs",
    "business_settings", "business_support_tickets", "business_ticket_replies", "calendar_events", "call_ai_requests", "call_contacts", "call_log", "call_opt_outs",
    "call_provider_accounts", "commands", "commerce_alerts", "commerce_connectors", "commerce_customers",
    "commerce_fees", "commerce_listings", "commerce_order_lines", "commerce_orders", "commerce_products",
    "commerce_returns", "commerce_settings", "conversation_turns", "conversations", "developer_projects",
    "developer_requests", "domain_events", "email_accounts", "email_drafts", "email_messages",
    "email_oauth_requests", "favorites", "finance_accounts", "finance_agent_decisions", "finance_bills",
    "finance_connections", "finance_debts", "finance_exec_intents", "finance_exec_settings", "finance_fx_rates",
    "finance_goals", "finance_guardian_changes", "finance_guardian_versions", "finance_holdings", "finance_imports",
    "finance_instruments", "finance_live_incidents", "finance_live_widenings", "finance_market_bars",
    "finance_paper_orders", "finance_paper_portfolios", "finance_plans", "finance_prices", "finance_settings",
    "finance_strategies", "finance_strategy_runs", "finance_transactions", "home_insight_states", "home_pins",
    "integration_configurations", "integration_credentials", "integration_health_checks", "kb_collections",
    "kb_items", "learning_notes", "learning_preferences", "learning_profiles", "learning_routines",
    "learning_signals", "learning_suggestions", "mail_account_grants", "mail_account_settings", "mail_accounts",
    "mail_attachments", "mail_contacts", "mail_drafts", "mail_forward_log", "mail_forward_rules",
    "mail_identities", "mail_labels", "mail_ops_counters", "mail_sync_state", "marketing_accounts",
    "marketing_campaigns", "marketing_posts", "marketing_preferences", "marketplace_installs",
    "meeting_connector_accounts", "meeting_transcript_segments", "meetings", "memories", "model_budgets",
    "model_usage_monthly", "notification_preferences", "push_devices", "quick_commands", "reports",
    "retrieval_chunks", "retrieval_sources", "setup_plan_items", "skill_installations", "tasks", "team_activity",
    "team_member_profiles", "tyre_alerts", "tyre_connections", "tyre_reports", "tyre_snapshots",
    "user_notifications", "voice_sessions", "voice_transcript_segments", "whatsapp_accounts",
    "whatsapp_contacts", "whatsapp_conversations", "whatsapp_messages", "work_item_attachments",
    "work_item_checklist", "work_item_comments", "work_items", "workspace_approval_policies",
    "workspace_autonomy_settings", "workspace_files", "workspace_invitations", "workspace_memberships",
    "workspace_model_configs",
)
STORAGE_KEYS = {"workspace_files": "storage_key", "mail_attachments": "storage_key"}
# Row policies that apply to every role look rows up in these tables (agent memories need a
# visible agent; mail rows admit grantees). PostgreSQL checks the purger's privilege on
# them even when its own policy already admits the row, so it may read these columns.
POLICY_LOOKUPS = {
    "agent_profiles": ("id", "tenant_id", "workspace_id"),
    "mail_account_grants": ("account_id", "grantee_user_id", "access"),
    # The after-delete triggers of memories and workspace_files (0015, security invoker)
    # delete their index rows by source.
    "retrieval_sources": ("source_type", "source_id"),
}


def _workspace_column(table: str) -> str | None:
    if table == "workspaces":
        return "id"
    return None if table in TENANT_WIDE else "workspace_id"


def _purge_predicate(table: str) -> str:
    """Rows of the session's tenant while a purging job of that tenant covers them."""
    column = _workspace_column(table)
    covers = "j.kind = 'organisation_deletion'"
    if column is not None:
        covers = f"({covers} or (j.kind = 'workspace_deletion' and j.workspace_id = {table}.{column}))"
    return (
        f"tenant_id = {TENANT}\n"  # nosec B608 - fixed table names
        f"          and exists (\n"
        f"            select 1 from {JOBS} j\n"
        f"            where j.tenant_id = {table}.tenant_id and j.status = 'purging' and {covers}\n"
        f"          )"
    )


def _purge_policy(table: str) -> None:
    """Grants and the purge policy for one tenant table (copy this for a new table)."""
    columns = ["tenant_id", "ctid"]
    workspace = _workspace_column(table)
    if workspace is not None:
        columns.append(workspace)
    if table in STORAGE_KEYS:
        columns.append(STORAGE_KEYS[table])
    op.execute(f"grant select ({', '.join(columns)}), delete on {table} to {PURGER}")
    predicate = _purge_predicate(table)
    op.execute(
        f"""
        create policy lifecycle_purge_{table} on {table}
        for all to {PURGER}
        using (
          {predicate}
        )
        with check (false)
        """
    )


# Every restrictive policy with a USING clause on the listed tables also admits the purger;
# the downgrade strips the clause again from the deparsed predicate.
EXTEND_RESTRICTIVE = """
    do $$
    declare
      pol record;
    begin
      for pol in
        select tablename, policyname, qual from pg_policies
        where schemaname = current_schema() and permissive = 'RESTRICTIVE' and qual is not null
          and tablename = any(:tables)
      loop
        if position('anum_tenant_purger' in pol.qual) = 0 then
          execute format(
            'alter policy %I on %I using ((%s) or current_user = %L)',
            pol.policyname, pol.tablename, pol.qual, 'anum_tenant_purger'
          );
        end if;
      end loop;
    end
    $$
"""
STRIP_RESTRICTIVE = """
    do $$
    declare
      pol record;
      expr text;
    begin
      for pol in
        select tablename, policyname, qual from pg_policies
        where schemaname = current_schema() and permissive = 'RESTRICTIVE' and qual is not null
          and tablename = any(:tables)
      loop
        expr := replace(pol.qual, ' OR (CURRENT_USER = ''anum_tenant_purger''::name)', '');
        if expr <> pol.qual then
          execute format('alter policy %I on %I using (%s)', pol.policyname, pol.tablename, expr);
        end if;
      end loop;
    end
    $$
"""


def _all_tables() -> tuple[str, ...]:
    return TENANT_WIDE + WORKSPACE_TABLES + ("workspaces",)


def _array_literal(tables: tuple[str, ...]) -> str:
    return "array[" + ", ".join(f"'{table}'" for table in tables) + "]::text[]"


def upgrade() -> None:
    op.execute(
        f"""
        do $$
        begin
          if not exists (select 1 from pg_roles where rolname = '{PURGER}') then
            create role {PURGER} nologin;
          end if;
        end
        $$
        """  # nosec B608 - fixed role name
    )
    op.execute(f"grant usage on schema public to {PURGER}")

    op.create_table(
        JOBS,
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), sa.ForeignKey("tenants.id", name="fk_tenant_lifecycle_jobs_tenant"), nullable=False),
        # The workspace the job was asked from (exports, organisation deletions: where the
        # receipt goes) or the workspace being deleted.
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("requested_by", sa.String(160), nullable=False),
        sa.Column("requested_at", TS, nullable=False),
        sa.Column("purge_after", TS, nullable=True),
        sa.Column("started_at", TS, nullable=True),
        sa.Column("finished_at", TS, nullable=True),
        sa.Column("cancelled_at", TS, nullable=True),
        sa.Column("cancelled_by", sa.String(160), nullable=True),
        sa.Column("lease_until", TS, nullable=True),
        sa.Column("expires_at", TS, nullable=True),
        sa.Column("archive_key", sa.String(600), nullable=True),
        sa.Column("archive_bytes", sa.BigInteger(), nullable=True),
        sa.Column("archive_sha256", sa.String(64), nullable=True),
        sa.Column("row_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("counts", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind in (" + ", ".join(f"'{k}'" for k in KINDS) + ")", name="ck_tenant_lifecycle_jobs_kind"),
        sa.CheckConstraint("status in (" + ", ".join(f"'{s}'" for s in STATUSES) + ")", name="ck_tenant_lifecycle_jobs_status"),
        sa.CheckConstraint("kind <> 'organisation_export' or status in ('queued', 'running', 'ready', 'failed', 'expired')", name="ck_tenant_lifecycle_jobs_export_status"),
        sa.CheckConstraint("kind = 'organisation_export' or (status in ('scheduled', 'cancelled', 'purging', 'completed', 'failed') and purge_after is not null)", name="ck_tenant_lifecycle_jobs_deletion_status"),
    )
    op.create_index("ix_tenant_lifecycle_jobs_scope", JOBS, ["tenant_id", "kind", "requested_at"])
    op.create_index(
        "uq_tenant_lifecycle_jobs_open_export", JOBS, ["tenant_id"], unique=True,
        postgresql_where=sa.text("kind = 'organisation_export' and status in ('queued', 'running')"),
    )
    op.create_index(
        "uq_tenant_lifecycle_jobs_open_org_deletion", JOBS, ["tenant_id"], unique=True,
        postgresql_where=sa.text("kind = 'organisation_deletion' and status in ('scheduled', 'purging')"),
    )
    op.create_index(
        "uq_tenant_lifecycle_jobs_open_ws_deletion", JOBS, ["tenant_id", "workspace_id"], unique=True,
        postgresql_where=sa.text("kind = 'workspace_deletion' and status in ('scheduled', 'purging')"),
    )
    op.execute(f"alter table {JOBS} enable row level security")
    op.execute(f"alter table {JOBS} force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_{JOBS} on {JOBS}
        using (tenant_id = {TENANT})
        with check (tenant_id = {TENANT})
        """
    )
    # Discovery of jobs that need work: ids, scope, status and times only.
    op.execute(
        f"grant select (id, tenant_id, workspace_id, kind, status, purge_after, lease_until, expires_at) on {JOBS} to anum_maintenance"
    )
    op.execute(
        f"""
        create policy maintenance_due_{JOBS} on {JOBS}
        for select to anum_maintenance
        using (
          status in ('queued', 'running', 'purging')
          or (status = 'scheduled' and purge_after <= now())
          or (status = 'ready' and expires_at <= now())
        )
        """
    )
    # The workspaces of an organisation whose export is running (ids only).
    op.execute("grant select (tenant_id, id) on workspaces to anum_maintenance")
    op.execute(
        f"""
        create policy maintenance_export_workspaces on workspaces
        for select to anum_maintenance
        using (
          exists (
            select 1 from {JOBS} j
            where j.tenant_id = workspaces.tenant_id and j.kind = 'organisation_export' and j.status = 'running'
          )
        )
        """  # nosec B608 - fixed table names
    )
    # The purger reads its own tenant's jobs (to evaluate the purge policies).
    op.execute(f"grant select (tenant_id, workspace_id, kind, status) on {JOBS} to {PURGER}")
    op.execute(
        f"""
        create policy purger_read_{JOBS} on {JOBS}
        for select to {PURGER}
        using (tenant_id = {TENANT})
        """
    )
    for table in _all_tables():
        _purge_policy(table)
    for table, columns in POLICY_LOOKUPS.items():
        op.execute(f"grant select ({', '.join(columns)}) on {table} to {PURGER}")
    op.execute(EXTEND_RESTRICTIVE.replace(":tables", _array_literal(_all_tables())))
    _deleted_organizations()


def _deleted_organizations() -> None:
    """The receipt the platform console shows for a deleted organisation: ids, dates and
    counts only. Written by the application role in the tenant's context; read by the
    operator who onboarded it through ``anum_platform_reader``; never purged."""
    op.create_table(
        DELETED,
        sa.Column("tenant_id", sa.String(80), sa.ForeignKey("tenants.id", name="fk_deleted_organizations_tenant"), primary_key=True),
        sa.Column("job_id", sa.String(80), nullable=False),
        sa.Column("operator_user_id", sa.String(160), nullable=True),
        sa.Column("display_name", sa.String(160), nullable=True),
        sa.Column("plan", sa.String(40), nullable=True),
        sa.Column("requested_by", sa.String(160), nullable=False),
        sa.Column("requested_at", TS, nullable=False),
        sa.Column("purge_started_at", TS, nullable=False),
        sa.Column("deleted_at", TS, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="purging"),
        sa.Column("row_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("counts", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("identity_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("identity_removed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("identity_detached", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("identity_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("identity_finished_at", TS, nullable=True),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status in ('purging', 'deleted')", name="ck_deleted_organizations_status"),
        sa.CheckConstraint(
            "identity_status in ('pending', 'removed', 'not_configured', 'failed')", name="ck_deleted_organizations_identity"
        ),
    )
    op.execute(f"alter table {DELETED} enable row level security")
    op.execute(f"alter table {DELETED} force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_{DELETED} on {DELETED}
        using (tenant_id = {TENANT})
        with check (tenant_id = {TENANT})
        """
    )
    op.execute(f"grant select ({', '.join(DELETED_COLUMNS)}) on {DELETED} to anum_platform_reader")
    op.execute(
        f"""
        create policy platform_reader_{DELETED} on {DELETED}
        for select to anum_platform_reader
        using (operator_user_id = nullif(current_setting('anum.user_id', true), ''))
        """
    )
    # Keycloak cleanup still to do (retried by the runner).
    op.execute(f"grant select (tenant_id, job_id, status, identity_status, identity_attempts) on {DELETED} to anum_maintenance")
    op.execute(
        f"""
        create policy maintenance_identity_{DELETED} on {DELETED}
        for select to anum_maintenance
        using (status = 'deleted' and identity_status = 'pending')
        """
    )


def downgrade() -> None:
    op.execute(f"drop table if exists {DELETED}")
    op.execute(STRIP_RESTRICTIVE.replace(":tables", _array_literal(_all_tables())))
    for table in _all_tables():
        op.execute(f"drop policy if exists lifecycle_purge_{table} on {table}")
        op.execute(f"revoke all on {table} from {PURGER}")
    op.execute("drop policy if exists maintenance_export_workspaces on workspaces")
    op.execute("revoke select (tenant_id, id) on workspaces from anum_maintenance")
    op.drop_table(JOBS)
    op.execute(f"revoke usage on schema public from {PURGER}")
