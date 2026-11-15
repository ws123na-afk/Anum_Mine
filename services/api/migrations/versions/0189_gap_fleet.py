"""Fleet: the monthly client report's saved draft per fleet and month (expand only).

docs/vehicles.md#monthly-fleet-report.

* ``vehicle_fleet_reports``: one row per workspace (a client fleet lives in its own business workspace) and
  month: the company name and the owner's notes kept between visits, the AI summary paragraphs (per
  language, with the fingerprint of the figures they were written from, so a paragraph written from older
  figures is never put in the report) and the report's deliveries (channel, recipient, status, the approval
  and task, and the frozen report content each delivery attaches, so what is sent is what was approved).
  Unique on (tenant, workspace, month).

Row level security (forced): the tenant and workspace policy; the restrictive ``vehicle_fleet_report_writer``
policies, so a row is inserted or changed only by the person it names as ``updated_by``; and
``lifecycle_purge_vehicle_fleet_reports`` with its grants (0079's pattern). No other role reads the table.

Chained after ``0188_pf_fx_matching`` (the coordinator renumbers at merge). Expand only: a new table that the
previous release never reads. Rollback: ``alembic downgrade 0188_pf_fx_matching`` drops it (the saved notes,
summaries and delivery history go with it; the vehicles and their records are untouched).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0189_gap_fleet"
down_revision = "0188_pf_fx_matching"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "vehicle_fleet_reports",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("company_name", sa.String(120), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("summaries", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("deliveries", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_vehicle_fleet_reports_workspace"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "month", name="uq_vehicle_fleet_reports_month"),
        sa.CheckConstraint("extract(day from month) = 1", name="ck_vehicle_fleet_reports_month"),
        sa.CheckConstraint("char_length(notes) <= 4000", name="ck_vehicle_fleet_reports_notes"),
        sa.CheckConstraint("jsonb_typeof(summaries) = 'object'", name="ck_vehicle_fleet_reports_summaries"),
        sa.CheckConstraint("jsonb_typeof(deliveries) = 'array'", name="ck_vehicle_fleet_reports_deliveries"),
    )
    op.execute("alter table vehicle_fleet_reports enable row level security")
    op.execute("alter table vehicle_fleet_reports force row level security")
    op.execute(
        "create policy tenant_isolation_vehicle_fleet_reports on vehicle_fleet_reports "
        "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
        "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')) "
        "with check (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
        "and workspace_id = nullif(current_setting('anum.workspace_id', true), ''))"
    )
    op.execute(
        "create policy vehicle_fleet_report_writer_insert on vehicle_fleet_reports as restrictive for insert "
        "with check (updated_by = nullif(current_setting('anum.user_id', true), ''))"
    )
    op.execute(
        "create policy vehicle_fleet_report_writer_update on vehicle_fleet_reports as restrictive for update "
        # Any row of the workspace may be rewritten (by its new writer); the purger is named so the lifecycle purge is never
        # hidden by a restrictive policy (test_postgres_tenant_lifecycle).
        "using (updated_by is not null or current_user = 'anum_tenant_purger'::name) "
        "with check (updated_by = nullif(current_setting('anum.user_id', true), ''))"
    )
    op.execute("grant select (tenant_id, ctid, workspace_id), delete on vehicle_fleet_reports to anum_tenant_purger")
    op.execute(
        "create policy lifecycle_purge_vehicle_fleet_reports on vehicle_fleet_reports for all to anum_tenant_purger "
        "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
        "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = vehicle_fleet_reports.tenant_id and j.status = 'purging' "
        "and (j.kind = 'organisation_deletion' or "
        "(j.kind = 'workspace_deletion' and j.workspace_id = vehicle_fleet_reports.workspace_id)))) "
        "with check (false)"
    )


def downgrade() -> None:
    op.drop_table("vehicle_fleet_reports")
