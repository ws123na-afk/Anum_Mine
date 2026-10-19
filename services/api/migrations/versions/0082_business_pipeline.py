"""Business prospecting pipeline and outreach outbox (docs/business.md#prospecting-pipeline).

Six workspace tables under forced RLS on ``anum.tenant_id`` and ``anum.workspace_id``
(``tenant_isolation_<table>``, the 0040 and 0070 pattern):

* ``business_outreach_settings``: one row per workspace (unique scope): the signals the owner
  confirmed, the daily page-fetch cap, sending (off by default), per-channel daily quotas,
  the bounce threshold and the pause (kill switch) with its reason.
* ``business_target_profiles``: one per product (unique ``product_id``): the product's target
  customer profile (industries, company sizes, regions, GCC first, roles, keywords, disqualifiers)
  and its value proposition in English and Arabic. Discovery runs, product fit and drafts read it.
* ``business_observations``: one signal seen on one public page (value, URL, excerpt, the day it
  was read), tied to the lead and the evidence row of that page.
* ``business_pipeline_runs``: discovery and profile runs with the estimate a person confirmed,
  progress steps and what the run used.
* ``business_outbox``: outbound messages tied to a lead, its contact and an approved draft, with
  the content hash an approval binds; sending is off until an owner turns it on.
* ``business_replies``: replies, opt-outs and bounces per lead (no message text), which stop
  follow-ups and feed the bounce kill switch.

References carry the scope (``(tenant_id, workspace_id, <id>)`` composite foreign keys onto the
0070 tables' ``uq_<table>_scope_id``), so a row can never point into another tenant or workspace.
No discovery role reads these tables.

Organisation and workspace deletion (migration ``0079_tenant_lifecycle``): all six tables get
the ``anum_tenant_purger`` grants (scope columns only, plus delete) and the purge policy
``lifecycle_purge_<table>``, copied from 0079's ``_purge_policy`` for workspace tables. No
other role is granted anything.

Expand only: new tables, indexes and the purger's grants and policies. The previous release
ignores them.

Numbered 0082 and chained after ``0087_email_draft_bcc``, the head of ``main``; renumber and re-chain at
merge if another migration lands first.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0082_business_pipeline"
down_revision = "0087_email_draft_bcc"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = (
    "business_outreach_settings",
    "business_target_profiles",
    "business_observations",
    "business_pipeline_runs",
    "business_outbox",
    "business_replies",
)
PURGER = "anum_tenant_purger"
JOBS = "tenant_lifecycle_jobs"
TS = sa.DateTime(timezone=True)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{value}'" for value in values) + ")"


def _jsonb(name: str, default: str = "'[]'::jsonb") -> sa.Column:
    return sa.Column(name, postgresql.JSONB(), nullable=False, server_default=sa.text(default))


def _base(table: str) -> list:  # type: ignore[type-arg]
    return [
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "id", name=f"uq_{table}_scope_id"),
    ]


def _ref(table: str, column: str, target: str, *, cascade: bool) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id", column],
        [f"{target}.tenant_id", f"{target}.workspace_id", f"{target}.id"],
        name=f"fk_{table}_{column}",
        ondelete="CASCADE" if cascade else "RESTRICT",
    )


def _enable_rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_{table} on {table}
        using (
          {PREDICATE}
        )
        with check (
          {PREDICATE}
        )
        """
    )


def _purge_policy(table: str) -> None:
    """Deletion of the organisation or of one workspace may remove these rows (0079's ``_purge_policy``)."""
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to {PURGER}")
    op.execute(
        f"""
        create policy lifecycle_purge_{table} on {table}
        for all to {PURGER}
        using (
          tenant_id = nullif(current_setting('anum.tenant_id', true), '')
          and exists (
            select 1 from {JOBS} j
            where j.tenant_id = {table}.tenant_id and j.status = 'purging'
              and (j.kind = 'organisation_deletion'
                   or (j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id))
          )
        )
        with check (false)
        """  # nosec B608 - fixed table names
    )


def upgrade() -> None:
    op.create_table(
        "business_outreach_settings",
        *_base("business_outreach_settings"),
        _jsonb("signals", "'{}'::jsonb"),
        sa.Column("fetches_per_day", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("pages_per_lead", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("sending_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        _jsonb("daily_quota", "'{}'::jsonb"),
        sa.Column("max_bounce_rate_pct", sa.Float(), nullable=False, server_default="5"),
        sa.Column("follow_up_max", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("follow_up_min_days", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("paused_reason", sa.String(300), nullable=False, server_default=""),
        sa.Column("paused_at", TS, nullable=True),
        sa.Column("updated_by", sa.String(160), nullable=True),
        # One settings row per workspace.
        sa.UniqueConstraint("tenant_id", "workspace_id", name="uq_business_outreach_settings_scope"),
        sa.CheckConstraint("fetches_per_day between 0 and 2000", name="ck_business_outreach_settings_fetches"),
        sa.CheckConstraint("pages_per_lead between 1 and 8", name="ck_business_outreach_settings_pages"),
    )

    op.create_table(
        "business_target_profiles",
        *_base("business_target_profiles"),
        sa.Column("product_id", sa.String(80), nullable=False),
        _jsonb("industries"),
        _jsonb("company_sizes"),
        _jsonb("regions"),
        _jsonb("roles"),
        _jsonb("keywords"),
        _jsonb("disqualifiers"),
        _jsonb("trigger_events"),
        sa.Column("company_size_note", sa.String(300), nullable=False, server_default=""),
        sa.Column("value_proposition", sa.Text(), nullable=False, server_default=""),
        sa.Column("value_proposition_ar", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_by", sa.String(160), nullable=True),
        _ref("business_target_profiles", "product_id", "business_products", cascade=True),
        # One target profile per product.
        sa.UniqueConstraint("tenant_id", "workspace_id", "product_id", name="uq_business_target_profiles_product"),
    )

    op.create_table(
        "business_observations",
        *_base("business_observations"),
        sa.Column("lead_id", sa.String(80), nullable=False),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("run_id", sa.String(80), nullable=True),
        sa.Column("evidence_id", sa.String(80), nullable=True),
        sa.Column("signal_code", sa.String(60), nullable=False),
        sa.Column("value_num", sa.Float(), nullable=True),
        sa.Column("value_text", sa.String(300), nullable=False, server_default=""),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("excerpt", sa.String(600), nullable=False, server_default=""),
        sa.Column("observed_on", sa.Date(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(160), nullable=False),
        _ref("business_observations", "lead_id", "business_leads", cascade=True),
        _ref("business_observations", "account_id", "business_accounts", cascade=True),
        sa.CheckConstraint("url ~ '^https?://'", name="ck_business_observations_url"),
        sa.CheckConstraint("confidence between 0 and 1", name="ck_business_observations_confidence"),
    )
    op.create_index("ix_business_observations_lead", "business_observations", ["tenant_id", "workspace_id", "lead_id", "observed_on"])

    op.create_table(
        "business_pipeline_runs",
        *_base("business_pipeline_runs"),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("product_id", sa.String(80), nullable=True),
        _jsonb("plan", "'{}'::jsonb"),
        _jsonb("estimate", "'{}'::jsonb"),
        _jsonb("used", "'{}'::jsonb"),
        _jsonb("steps"),
        _jsonb("results", "'{}'::jsonb"),
        sa.Column("stop_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("stop_reason", sa.String(300), nullable=False, server_default=""),
        sa.Column("confirmed_by", sa.String(160), nullable=True),
        sa.Column("confirmed_at", TS, nullable=True),
        sa.Column("heartbeat_at", TS, nullable=True),
        sa.Column("finished_at", TS, nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.CheckConstraint(_in("kind", ("discover", "profile")), name="ck_business_pipeline_runs_kind"),
        sa.CheckConstraint(_in("status", ("estimated", "running", "succeeded", "partial", "stopped", "failed")), name="ck_business_pipeline_runs_status"),
    )
    op.create_index("ix_business_pipeline_runs_scope_created_at", "business_pipeline_runs", ["tenant_id", "workspace_id", "created_at", "id"])

    op.create_table(
        "business_outbox",
        *_base("business_outbox"),
        sa.Column("lead_id", sa.String(80), nullable=False),
        sa.Column("contact_id", sa.String(80), nullable=False),
        sa.Column("draft_id", sa.String(80), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("follow_up_of", sa.String(80), nullable=True),
        sa.Column("sequence_id", sa.String(80), nullable=False),
        sa.Column("recipient", sa.String(254), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        _jsonb("gate_issues"),
        sa.Column("approval_id", sa.String(80), nullable=True),
        sa.Column("approval_task_id", sa.String(80), nullable=True),
        sa.Column("send_after", TS, nullable=True),
        sa.Column("sent_at", TS, nullable=True),
        sa.Column("provider_ref", sa.String(200), nullable=False, server_default=""),
        sa.Column("note", sa.String(500), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(160), nullable=False),
        _ref("business_outbox", "lead_id", "business_leads", cascade=True),
        _ref("business_outbox", "contact_id", "business_contacts", cascade=True),
        _ref("business_outbox", "draft_id", "business_drafts", cascade=True),
        sa.CheckConstraint(_in("channel", ("email", "whatsapp")), name="ck_business_outbox_channel"),
        sa.CheckConstraint(_in("status", ("scheduled", "pending_approval", "held", "sending", "sent", "failed", "cancelled", "rejected", "needs_review")), name="ck_business_outbox_status"),
        sa.CheckConstraint("step between 1 and 6", name="ck_business_outbox_step"),
    )
    # One live outbox row per draft, and one live row per step of a sequence (the follow-up cap);
    # a sent row is final, a cancelled, failed or rejected one frees its place.
    live = "status in ('scheduled', 'pending_approval', 'held', 'sending', 'sent', 'needs_review')"
    op.create_index(
        "uq_business_outbox_open_draft",
        "business_outbox",
        ["tenant_id", "workspace_id", "draft_id"],
        unique=True,
        postgresql_where=sa.text(live),
    )
    op.create_index(
        "uq_business_outbox_sequence_step",
        "business_outbox",
        ["tenant_id", "workspace_id", "sequence_id", "step"],
        unique=True,
        postgresql_where=sa.text(live),
    )
    op.create_index("ix_business_outbox_lead", "business_outbox", ["tenant_id", "workspace_id", "lead_id", "status"])
    op.create_index("ix_business_outbox_scope_updated_at", "business_outbox", ["tenant_id", "workspace_id", "updated_at", "id"])

    op.create_table(
        "business_replies",
        *_base("business_replies"),
        sa.Column("lead_id", sa.String(80), nullable=False),
        sa.Column("contact_id", sa.String(80), nullable=True),
        sa.Column("outbox_id", sa.String(80), nullable=True),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("recipient_hash", sa.String(64), nullable=True),
        sa.Column("cancelled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("note", sa.String(300), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(160), nullable=False),
        _ref("business_replies", "lead_id", "business_leads", cascade=True),
        sa.CheckConstraint(_in("kind", ("reply", "opt_out", "bounce", "escalate")), name="ck_business_replies_kind"),
    )
    op.create_index("ix_business_replies_scope_created_at", "business_replies", ["tenant_id", "workspace_id", "created_at", "id"])

    for table in TABLES:
        _enable_rls(table)
        _purge_policy(table)


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"drop policy if exists lifecycle_purge_{table} on {table}")
    for table in reversed(TABLES):
        op.drop_table(table)
