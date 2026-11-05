"""Finance Guardian review queue (expand only). docs/finance.md#review-queue.

* ``finance_guardian_reviews``: the person's decision on one payment a Guardian rule flagged
  (``expected``, ``reviewed`` or ``snoozed`` until a time), one row per person and transaction,
  with the amount and currency it was decided on (a corrected payment waits for review again)
  and the rule codes. A note only: it never moves, holds or approves money.

Forced RLS with ``tenant_isolation_<table>``, the restrictive ``owner_only_<table>`` policy that
also admits the tenant purge role, and ``lifecycle_purge_<table>``, like ``0134``.

Expand only: one new table. The previous release ignores it. Chained after ``0139_finance_pdf_templates``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0140_finance_guardian_reviews"
down_revision = "0139_finance_pdf_templates"
branch_labels = None
depends_on = None

TABLE = "finance_guardian_reviews"
TS = sa.DateTime(timezone=True)
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"


def _rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(f"create policy tenant_isolation_{table} on {table} using ({PREDICATE}) with check ({PREDICATE})")
    op.execute(f"create policy owner_only_{table} on {table} as restrictive "
               f"using (({OWNER_PREDICATE}) or (current_user = 'anum_tenant_purger'::name)) with check ({OWNER_PREDICATE})")
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to anum_tenant_purger")
    op.execute(
        f"create policy lifecycle_purge_{table} on {table} for all to anum_tenant_purger "  # nosec B608: fixed table name
        "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
        "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = "
        f"{table}.tenant_id and j.status = 'purging' "
        "and (j.kind = 'organisation_deletion' or "
        f"(j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id)))) "
        "with check (false)"
    )


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
        sa.Column("transaction_id", sa.String(80), nullable=False),
        sa.Column("decision", sa.String(12), nullable=False),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("codes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("snoozed_until", TS, nullable=True),
        sa.Column("decided_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{TABLE}_workspace"),
        sa.CheckConstraint("decision in ('expected', 'reviewed', 'snoozed')", name=f"ck_{TABLE}_decision"),
        sa.CheckConstraint("(decision = 'snoozed') = (snoozed_until is not null)", name=f"ck_{TABLE}_snooze"),
        sa.CheckConstraint("amount > 0", name=f"ck_{TABLE}_amount"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=f"ck_{TABLE}_currency"),
        # One decision per person and payment: a new decision replaces the last.
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "transaction_id", name=f"uq_{TABLE}_transaction"),
    )
    _rls(TABLE)


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.execute(f"drop policy if exists lifecycle_purge_{TABLE} on {TABLE}")
    op.drop_table(TABLE)
