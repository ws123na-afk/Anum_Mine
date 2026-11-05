"""The weekly Money advisor (expand only). docs/finance.md#money-advisor.

* ``finance_advisor_reports``: one weekly money note per person and advice week (``week_start``, unique),
  the note itself as JSON (figures worked out from the person's own ledger, never sent anywhere), what
  wrote it (``schedule`` or ``manual``) and when its weekly notification went out (``delivered_at``, set
  once, so the notice is sent once per week).
* ``finance_advisor_settings``: the person's switches: on or off and the weekday the note is written.

Both tables: forced RLS like ``0139``: ``tenant_isolation_<table>``, a restrictive ``owner_only_<table>``
policy on ``owner_user_id = anum.user_id`` (also admitting the tenant purge role, which can only delete)
and ``lifecycle_purge_<table>``.

Discovery for the daily job (``python -m anum_api.voice_retention``, reported under ``finance_advisor``),
migration 0057's pattern, never BYPASSRLS: ``anum_maintenance`` may read the scope columns
(``tenant_id``, ``workspace_id``, ``owner_user_id``) and ``archived`` of active ``finance_accounts`` and
nothing else; the restrictive owner policy on ``finance_accounts`` also admits the session whose
``current_user`` is ``anum_maintenance`` (a login that merely inherits the role never is). The work then
runs as the application role in each person's own scope.

Expand only: new tables, a column grant, a policy and a widened restrictive policy that only the
discovery role can use. The previous release ignores them. Chained after ``0139_finance_pdf_templates``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0143_finance_money_advisor"
down_revision = "0139_finance_pdf_templates"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"
REPORTS = "finance_advisor_reports"
SETTINGS = "finance_advisor_settings"


def _rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(f"create policy tenant_isolation_{table} on {table} using ({PREDICATE}) with check ({PREDICATE})")
    op.execute(f"create policy owner_only_{table} on {table} as restrictive "
               f"using (({OWNER_PREDICATE}) or (current_user = 'anum_tenant_purger'::name)) with check ({OWNER_PREDICATE})")


def upgrade() -> None:
    op.create_table(
        REPORTS,
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("trigger", sa.String(10), nullable=False),
        sa.Column("generated_at", TS, nullable=False),
        sa.Column("delivered_at", TS, nullable=True),
        sa.Column("note", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_finance_advisor_reports_workspace"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "week_start", name="uq_finance_advisor_reports_week"),
        sa.CheckConstraint("trigger in ('schedule', 'manual')", name="ck_finance_advisor_reports_trigger"),
        sa.CheckConstraint("jsonb_typeof(note) = 'object'", name="ck_finance_advisor_reports_note"),
    )
    op.create_table(
        SETTINGS,
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("weekday", sa.SmallInteger(), nullable=False, server_default="6"),
        sa.Column("updated_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "owner_user_id", name="pk_finance_advisor_settings"),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_finance_advisor_settings_workspace"),
        sa.CheckConstraint("weekday between 0 and 6", name="ck_finance_advisor_settings_weekday"),
    )
    _rls(REPORTS)
    _rls(SETTINGS)
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {REPORTS} to anum_tenant_purger")
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {SETTINGS} to anum_tenant_purger")
    op.execute("create policy lifecycle_purge_finance_advisor_reports on finance_advisor_reports for all to anum_tenant_purger "
        "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
        "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = "
        "finance_advisor_reports.tenant_id and j.status = 'purging' "
        "and (j.kind = 'organisation_deletion' or "
        "(j.kind = 'workspace_deletion' and j.workspace_id = finance_advisor_reports.workspace_id)))) "
        "with check (false)")
    op.execute("create policy lifecycle_purge_finance_advisor_settings on finance_advisor_settings for all to anum_tenant_purger "
        "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
        "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = "
        "finance_advisor_settings.tenant_id and j.status = 'purging' "
        "and (j.kind = 'organisation_deletion' or "
        "(j.kind = 'workspace_deletion' and j.workspace_id = finance_advisor_settings.workspace_id)))) "
        "with check (false)")

    # Discovery for the weekly note: who has an active finance account. Scope columns only, no write.
    op.execute("grant select (tenant_id, workspace_id, owner_user_id, archived) on finance_accounts to anum_maintenance")
    op.execute("create policy maintenance_advisor_finance_accounts on finance_accounts for select to anum_maintenance using (not archived)")
    op.execute(f"alter policy owner_only_finance_accounts on finance_accounts using (({OWNER_PREDICATE}) "
               "or (current_user = 'anum_tenant_purger'::name) or (current_user = 'anum_maintenance'::name))")


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.execute(f"alter policy owner_only_finance_accounts on finance_accounts using (({OWNER_PREDICATE}) "
               "or (current_user = 'anum_tenant_purger'::name))")
    op.execute("drop policy if exists maintenance_advisor_finance_accounts on finance_accounts")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke select (tenant_id, workspace_id, owner_user_id, archived) on finance_accounts from anum_maintenance;
          end if;
        end
        $$
        """
    )
    op.execute(f"drop policy if exists lifecycle_purge_{SETTINGS} on {SETTINGS}")
    op.execute(f"drop policy if exists lifecycle_purge_{REPORTS} on {REPORTS}")
    op.drop_table(SETTINGS)
    op.drop_table(REPORTS)
