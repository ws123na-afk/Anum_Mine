"""One lead list: WhatsApp Sales CRM leads become Business leads (docs/business.md#whatsapp-leads).

Expand only:

* ``business_leads`` gains four nullable columns: ``channel`` (``whatsapp`` or null), the
  WhatsApp conversation the lead came from (``channel_conversation_id``), the normalised phone
  number it came from (``channel_phone``, used for duplicate suggestions) and the
  ``sales_lead_id`` of the WhatsApp Sales CRM row it was mapped from. A partial unique index
  keeps one Business lead per WhatsApp lead, so two people mapping the same row at once cannot
  create two leads. The previous release never writes these columns and ignores them.
* ``business_lead_pair_decisions``: the owner's answer to a duplicate suggestion ("the same
  person, merged" or "different people, keep separate"), one row per pair, so a suggestion the
  owner answered is not asked again. Forced row level security on the tenant and workspace
  (``tenant_isolation_<table>``, the 0070 pattern) and the purge grants and policy of
  ``0079_tenant_lifecycle`` (``lifecycle_purge_<table>``).

No row is copied here and ``sales_leads`` is not touched. Every table involved has forced row
level security bound to ``anum.tenant_id``, so a migration (which runs with no tenant context and
never with ``BYPASSRLS``) cannot read the rows to map them. The mapping runs in the application,
inside each workspace's own context: ``POST /api/v1/business/lead-sources/whatsapp/map-all`` and
per lead from the Leads list, while the Leads list keeps reading both tables until a later
contract step drops the old list (docs/business.md#whatsapp-leads).
"""

import sqlalchemy as sa
from alembic import op

revision = "0125_unify_whatsapp_leads"
down_revision = "0124_ibkr_purge_policy"
branch_labels = None
depends_on = None

TABLE = "business_lead_pair_decisions"
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
PURGER = "anum_tenant_purger"
JOBS = "tenant_lifecycle_jobs"
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("business_leads", sa.Column("channel", sa.String(20), nullable=True))
    op.add_column("business_leads", sa.Column("channel_conversation_id", sa.String(80), nullable=True))
    op.add_column("business_leads", sa.Column("channel_phone", sa.String(20), nullable=True))
    op.add_column("business_leads", sa.Column("sales_lead_id", sa.String(80), nullable=True))
    op.create_check_constraint("ck_business_leads_channel", "business_leads", "channel is null or channel in ('whatsapp')")
    op.execute(
        "create unique index uq_business_leads_sales_lead on business_leads "
        "(tenant_id, workspace_id, sales_lead_id) where sales_lead_id is not null"
    )
    op.execute(
        "create index ix_business_leads_channel_phone on business_leads "
        "(tenant_id, workspace_id, channel_phone) where channel_phone is not null"
    )

    op.create_table(
        TABLE,
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("pair_key", sa.String(200), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("decided_by", sa.String(160), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{TABLE}_workspace"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "id", name=f"uq_{TABLE}_scope_id"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "pair_key", name=f"uq_{TABLE}_pair"),
        sa.CheckConstraint("decision in ('separate', 'merged')", name=f"ck_{TABLE}_decision"),
    )
    _enable_rls(TABLE)
    _purge_policy(TABLE)


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
        """  # nosec B608 - fixed table name
    )


def downgrade() -> None:
    op.drop_table(TABLE)
    op.execute("drop index if exists ix_business_leads_channel_phone")
    op.execute("drop index if exists uq_business_leads_sales_lead")
    op.drop_constraint("ck_business_leads_channel", "business_leads", type_="check")
    for column in ("sales_lead_id", "channel_phone", "channel_conversation_id", "channel"):
        op.drop_column("business_leads", column)
