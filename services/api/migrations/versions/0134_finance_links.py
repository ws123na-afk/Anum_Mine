"""Finance links, business allocations and iPhone device events (expand only). docs/finance-links.md.

* ``finance_links``: a canonical finance transaction (``finance_journal_entries.canonical_id``,
  which survives corrections) linked to a target in another module (a vehicle, a purchase, a
  document, a business allocation, a trip, a project) with an amount, a status and an
  idempotency key. Links never post: they are views of the one posting. A constraint trigger
  (deferred to commit) refuses active links of one dimension whose total exceeds the
  transaction's amount in that currency.
* ``business_ledger_allocations``: the row a business workspace sees when its member allocates
  a personal transaction (or part of it) to that business. Amount, currency, category, project,
  date and a short source reference only: never the bank, the account, the card or the message.
  Readable by the business workspace's members under the ordinary workspace policy.
* ``finance_device_events``: one row per iPhone integration request (accepted, failed, refused,
  test) per device and integration, for per-integration rate limits, last connection and the
  failed events list on Settings, Integrations, iPhone. Codes only, never a body.

Owner tables (``finance_links``, ``finance_device_events``) get forced RLS with
``tenant_isolation_<table>``, the restrictive ``owner_only_<table>`` policy that also admits the
tenant purge role, and ``lifecycle_purge_<table>``, like ``0131``. The allocation table gets the
tenant policy and the purge policy.

Expand only: new tables, functions and a trigger on a new table. The previous release ignores
them. Chained after ``0132_work_attendance``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0134_finance_links"
down_revision = "0132_work_attendance"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"
OWNER_TABLES = ("finance_links", "finance_device_events")
TABLES = (*OWNER_TABLES, "business_ledger_allocations")


def _owned() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
    ]


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace")


def _rls(table: str, *, owner: bool) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(f"create policy tenant_isolation_{table} on {table} using ({PREDICATE}) with check ({PREDICATE})")
    if owner:
        op.execute(f"create policy owner_only_{table} on {table} as restrictive "
                   f"using (({OWNER_PREDICATE}) or (current_user = 'anum_tenant_purger'::name)) with check ({OWNER_PREDICATE})")
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to anum_tenant_purger")
    purge_policy = (f"create policy lifecycle_purge_{table} on {table} for all to anum_tenant_purger "  # nosec B608: fixed table name
        "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
        "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = "
        f"{table}.tenant_id and j.status = 'purging' "
        "and (j.kind = 'organisation_deletion' or "
        f"(j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id)))) "
        "with check (false)")
    op.execute(purge_policy)


def upgrade() -> None:
    op.create_table(
        "finance_links",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("canonical_id", sa.String(80), nullable=False),
        sa.Column("dimension", sa.String(20), nullable=False),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("target_type", sa.String(40), nullable=False),
        sa.Column("target_id", sa.String(80), nullable=False),
        sa.Column("target_workspace_id", sa.String(80), nullable=True),
        sa.Column("amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("status", sa.String(14), nullable=False),
        sa.Column("status_reason", sa.String(40), nullable=True),
        sa.Column("source_event_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("document_id", sa.String(80), nullable=True),
        sa.Column("idem_key", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("voided_at", TS, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        _workspace_fk("finance_links"),
        sa.CheckConstraint("dimension in ('vehicle', 'purchase', 'document', 'business', 'trip', 'project')", name="ck_finance_links_dimension"),
        sa.CheckConstraint("role in ('payment', 'refund', 'deposit', 'evidence')", name="ck_finance_links_role"),
        sa.CheckConstraint("status in ('active', 'needs_review', 'void')", name="ck_finance_links_status"),
        sa.CheckConstraint("(amount is null) = (currency is null)", name="ck_finance_links_money"),
        sa.CheckConstraint("amount is null or amount > 0", name="ck_finance_links_positive"),
        sa.CheckConstraint("currency is null or currency ~ '^[A-Z]{3}$'", name="ck_finance_links_currency"),
        sa.CheckConstraint("(role = 'evidence') = (amount is null)", name="ck_finance_links_evidence"),
        sa.CheckConstraint("(dimension = 'business') = (target_workspace_id is not null)", name="ck_finance_links_business"),
        sa.CheckConstraint("(status = 'void') = (voided_at is not null)", name="ck_finance_links_void"),
        sa.CheckConstraint("jsonb_typeof(source_event_ids) = 'array'", name="ck_finance_links_sources"),
    )
    op.create_index("ix_finance_links_canonical", "finance_links", ["tenant_id", "workspace_id", "owner_user_id", "canonical_id", "dimension"])
    op.create_index("ix_finance_links_target", "finance_links", ["tenant_id", "workspace_id", "owner_user_id", "dimension", "target_type", "target_id"])
    # One live link per idempotency key: a retried request finds it; a voided link can be made again.
    op.execute("create unique index uq_finance_links_idem on finance_links (tenant_id, workspace_id, owner_user_id, idem_key) where status <> 'void'")

    op.create_table(
        "business_ledger_allocations",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("source_link_id", sa.String(80), nullable=False),
        sa.Column("source_workspace_id", sa.String(80), nullable=False),
        sa.Column("allocated_by", sa.String(160), nullable=False),
        sa.Column("source_ref", sa.String(12), nullable=False),
        sa.Column("posted_on", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("project_id", sa.String(80), nullable=True),
        sa.Column("memo", sa.String(200), nullable=True),
        sa.Column("treatment", sa.String(20), nullable=False),
        sa.Column("status", sa.String(14), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("business_ledger_allocations"),
        sa.UniqueConstraint("tenant_id", "source_link_id", name="uq_business_ledger_allocations_link"),
        sa.CheckConstraint("amount > 0", name="ck_business_ledger_allocations_amount"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_business_ledger_allocations_currency"),
        sa.CheckConstraint("treatment in ('owner_contribution', 'reimbursable')", name="ck_business_ledger_allocations_treatment"),
        sa.CheckConstraint("status in ('active', 'needs_review')", name="ck_business_ledger_allocations_status"),
        sa.CheckConstraint("source_workspace_id <> workspace_id", name="ck_business_ledger_allocations_other"),
    )
    op.create_index("ix_business_ledger_allocations_ws", "business_ledger_allocations", ["tenant_id", "workspace_id", "posted_on"])

    op.create_table(
        "finance_device_events",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("device_id", sa.String(80), nullable=False),
        sa.Column("integration", sa.String(40), nullable=False),
        sa.Column("scope", sa.String(30), nullable=False),
        sa.Column("outcome", sa.String(10), nullable=False),
        sa.Column("code", sa.String(40), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("finance_device_events"),
        sa.CheckConstraint("outcome in ('ok', 'failed', 'denied', 'test')", name="ck_finance_device_events_outcome"),
    )
    op.create_index("ix_finance_device_events_owner", "finance_device_events",
                    ["tenant_id", "workspace_id", "owner_user_id", "integration", "created_at"])
    op.create_index("ix_finance_device_events_device", "finance_device_events",
                    ["tenant_id", "workspace_id", "owner_user_id", "device_id", "scope", "created_at"])

    # Active links of one dimension never add up to more than the transaction: the positive postings
    # (debits) in that currency of the chain's live entry. Checked when the transaction commits, inside
    # the owner's RLS scope; writers also hold the owner's advisory lock, so two links cannot race.
    op.execute("""
        create function finance_links_within_amount() returns trigger language plpgsql as $$
        declare
            cap numeric;
            used numeric;
        begin
            if new.status <> 'active' or new.amount is null then
                return null;
            end if;
            select coalesce(sum(p.amount), 0) into cap
              from finance_postings p join finance_journal_entries e on e.id = p.entry_id
             where e.tenant_id = new.tenant_id and e.workspace_id = new.workspace_id and e.owner_user_id = new.owner_user_id
               and e.canonical_id = new.canonical_id and e.status = 'posted' and e.source not in ('reversal', 'clearing')
               and p.amount > 0 and p.currency = new.currency;
            select coalesce(sum(l.amount), 0) into used
              from finance_links l
             where l.tenant_id = new.tenant_id and l.workspace_id = new.workspace_id and l.owner_user_id = new.owner_user_id
               and l.canonical_id = new.canonical_id and l.dimension = new.dimension and l.currency = new.currency
               and l.status = 'active';
            if used > cap then
                raise exception 'finance links on % exceed the transaction amount', new.canonical_id using errcode = '23514';
            end if;
            return null;
        end $$
    """)
    op.execute("""
        create constraint trigger finance_links_within_amount after insert or update on finance_links
        deferrable initially deferred for each row execute function finance_links_within_amount()
    """)

    for table in OWNER_TABLES:
        _rls(table, owner=True)
    _rls("business_ledger_allocations", owner=False)


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.execute("drop trigger if exists finance_links_within_amount on finance_links")
    for table in reversed(TABLES):
        op.execute(f"drop policy if exists lifecycle_purge_{table} on {table}")
        op.drop_table(table)
    op.execute("drop function if exists finance_links_within_amount()")
