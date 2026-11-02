"""The setup plan's remaining gaps (docs/setup-plan.md): consent records for campaigns, per-agent
loop and retry limits, an approval-ageing reminder and the owner's Home layout.

New tables, each under forced RLS with the tenant-isolation policy of ``0095_agent_controls``:

* ``contact_consents`` (organisation-wide): one consent record per contact and channel, with
  the lawful basis, where it came from, when it was given and, once withdrawn, when and why.
  Contacts are stored as a per-tenant hash plus a masked form, like ``contact_suppressions``.
* ``contact_consent_settings`` (organisation-wide): whether a marketing send needs an active
  consent record for every recipient.
* ``agent_loop_limits`` (workspace): per agent, the most runs one task may have (the loop
  limit) and the most attempts an automation step that runs as the agent may make.
* ``approval_reminder_settings`` (workspace): after how many hours a pending approval reminds
  its approvers, or none.
* ``approval_reminders`` (workspace): one row per approval reminded, so it reminds once.

One nullable column: ``home_profiles.sections`` (the owner's chosen order of the web Home's
sections; null means the default order).

``anum_maintenance`` discovers due reminders (ids and timestamps only): a column grant and a
select policy on pending approvals, the reminder settings and the reminders already sent. It
is granted to the API login ``WITH INHERIT FALSE, SET TRUE``, so these policies never widen
the application role's own queries.

Organisation and workspace deletion (``0079_tenant_lifecycle``): every new table gets the
purge grants and policy and is listed in ``anum_api/tenant_lifecycle_tables.py``.

Expand only: the previous release ignores the new tables and the nullable column. Rollback
never reverses it. Numbered 0128 and chained after ``0127_stripe_payment_links``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0128_setup_plan_gaps"
down_revision = "0127_stripe_payment_links"
branch_labels = None
depends_on = None

TENANT_PREDICATE = "tenant_id = nullif(current_setting('anum.tenant_id', true), '')"
WORKSPACE_PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TENANT_TABLES = ("contact_consents", "contact_consent_settings")
WORKSPACE_TABLES = ("agent_loop_limits", "approval_reminder_settings", "approval_reminders")
PURGER = "anum_tenant_purger"
JOBS = "tenant_lifecycle_jobs"
TS = sa.DateTime(timezone=True)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{value}'" for value in values) + ")"


def _force_rls(table: str, predicate: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_{table} on {table}
        using (
          {predicate}
        )
        with check (
          {predicate}
        )
        """
    )


def _purge_policy(table: str, *, workspace: bool) -> None:
    columns = "tenant_id, ctid, workspace_id" if workspace else "tenant_id, ctid"
    covers = "j.kind = 'organisation_deletion'"
    if workspace:
        covers = f"({covers} or (j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id))"
    op.execute(f"grant select ({columns}), delete on {table} to {PURGER}")
    op.execute(
        f"""
        create policy lifecycle_purge_{table} on {table}
        for all to {PURGER}
        using (
          tenant_id = nullif(current_setting('anum.tenant_id', true), '')
          and exists (
            select 1 from {JOBS} j
            where j.tenant_id = {table}.tenant_id and j.status = 'purging' and {covers}
          )
        )
        with check (false)
        """  # nosec B608 - fixed table names
    )


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"
    )


def upgrade() -> None:
    op.create_table(
        "contact_consents",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), sa.ForeignKey("tenants.id", name="fk_contact_consents_tenant"), nullable=False),
        sa.Column("recipient_hash", sa.String(64), nullable=False),
        sa.Column("masked", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("lawful_basis", sa.String(40), nullable=False),
        sa.Column("source", sa.String(200), nullable=False),
        sa.Column("consented_at", TS, nullable=False),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("withdrawn_at", TS, nullable=True),
        sa.Column("withdrawn_reason", sa.String(300), nullable=True),
        sa.Column("withdrawn_by", sa.String(160), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "recipient_hash", "channel", name="uq_contact_consents_contact_channel"),
        sa.CheckConstraint("recipient_hash ~ '^[0-9a-f]{64}$'", name="ck_contact_consents_hash"),
        sa.CheckConstraint(_in("kind", ("phone", "email")), name="ck_contact_consents_kind"),
        sa.CheckConstraint(_in("channel", ("email", "whatsapp", "sms", "all")), name="ck_contact_consents_channel"),
        sa.CheckConstraint(
            _in("lawful_basis", ("consent", "contract", "legitimate_interest", "existing_customer")),
            name="ck_contact_consents_basis",
        ),
        sa.CheckConstraint("(withdrawn_at is null) = (withdrawn_by is null)", name="ck_contact_consents_withdrawn"),
    )
    op.create_index("ix_contact_consents_created", "contact_consents", ["tenant_id", "created_at"])
    op.create_table(
        "contact_consent_settings",
        sa.Column("tenant_id", sa.String(80), sa.ForeignKey("tenants.id", name="fk_contact_consent_settings_tenant"), nullable=False),
        sa.Column("require_for_marketing", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_contact_consent_settings"),
    )
    op.create_table(
        "agent_loop_limits",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("agent_id", sa.String(80), nullable=False),
        sa.Column("max_runs_per_task", sa.Integer(), nullable=True),
        sa.Column("max_step_attempts", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "agent_id", name="pk_agent_loop_limits"),
        sa.CheckConstraint("max_runs_per_task is null or max_runs_per_task between 1 and 20", name="ck_agent_loop_limits_runs"),
        sa.CheckConstraint("max_step_attempts is null or max_step_attempts between 1 and 10", name="ck_agent_loop_limits_attempts"),
        sa.CheckConstraint("max_runs_per_task is not null or max_step_attempts is not null", name="ck_agent_loop_limits_some"),
        _workspace_fk("agent_loop_limits"),
    )
    op.create_table(
        "approval_reminder_settings",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("reminder_hours", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", name="pk_approval_reminder_settings"),
        sa.CheckConstraint("reminder_hours between 1 and 23", name="ck_approval_reminder_settings_hours"),
        _workspace_fk("approval_reminder_settings"),
    )
    op.create_table(
        "approval_reminders",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("approval_id", sa.String(80), nullable=False),
        sa.Column("recipients", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "approval_id", name="pk_approval_reminders"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "approval_id"],
            ["approvals.tenant_id", "approvals.workspace_id", "approvals.id"],
            name="fk_approval_reminders_approval", ondelete="CASCADE",
        ),
    )
    op.add_column("home_profiles", sa.Column("sections", sa.dialects.postgresql.JSONB(), nullable=True))

    for table in TENANT_TABLES:
        _force_rls(table, TENANT_PREDICATE)
        _purge_policy(table, workspace=False)
    for table in WORKSPACE_TABLES:
        _force_rls(table, WORKSPACE_PREDICATE)
        _purge_policy(table, workspace=True)

    # Discovery of due approval reminders: ids and timestamps only, pending approvals only.
    op.execute("grant select (tenant_id, workspace_id, id, status, created_at, expires_at) on approvals to anum_maintenance")
    op.execute(
        """
        create policy maintenance_approval_reminders_due on approvals
        for select to anum_maintenance
        using (status = 'pending')
        """
    )
    op.execute("grant select (tenant_id, workspace_id, reminder_hours) on approval_reminder_settings to anum_maintenance")
    op.execute(
        """
        create policy maintenance_approval_reminder_settings on approval_reminder_settings
        for select to anum_maintenance
        using (true)
        """
    )
    op.execute("grant select (tenant_id, workspace_id, approval_id) on approval_reminders to anum_maintenance")
    op.execute(
        """
        create policy maintenance_approval_reminders on approval_reminders
        for select to anum_maintenance
        using (true)
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_approval_reminders_due on approvals")
    op.execute("drop policy if exists maintenance_approval_reminder_settings on approval_reminder_settings")
    op.execute("drop policy if exists maintenance_approval_reminders on approval_reminders")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke select (tenant_id, workspace_id, id, status, created_at, expires_at) on approvals from anum_maintenance;
          end if;
        end
        $$
        """
    )
    op.drop_column("home_profiles", "sections")
    for table in (*reversed(WORKSPACE_TABLES), *reversed(TENANT_TABLES)):
        op.execute(f"drop policy if exists lifecycle_purge_{table} on {table}")
        op.drop_table(table)
