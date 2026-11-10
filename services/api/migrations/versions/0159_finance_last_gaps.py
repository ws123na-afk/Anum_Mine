"""Dismissed receipt proposals and the owner's own VAT filing deadline (expand only).

docs/business-books.md#receipts-to-bills, docs/business-tax.md#filing-deadlines.

* ``business_book_receipt_dismissals``: one row per (bill, document) pair an owner or accountant dismissed in the
  Accountant review, so that pair is never proposed again; restoring it deletes the row. Unique per business, bill and
  document. A business table like ``0154``: forced RLS with ``tenant_isolation_<table>`` and ``lifecycle_purge_<table>``
  (the tenant purge role may only delete). No grant to a maintenance role. No foreign key to the bill: a bill or a
  document that goes away only leaves a row nothing proposes again, removed with the business.
* ``business_tax_settings.country`` (nullable, two capital letters): the filing country of a business whose
  jurisdiction is ``OTHER`` (Bahrain and Oman have built-in deadlines, Kuwait and Qatar have no VAT).
* ``business_tax_settings.filing_due_days`` (nullable, 1 to 120): the owner's own VAT return due day, in days after
  the filing period ends; it always wins over a built-in deadline.

Chained after ``0158_finance_depth``.

Expand only. The previous release ignores the new columns and table (it proposes dismissed pairs again, nothing
else changes). Rollback: ``alembic downgrade 0158_finance_depth`` drops the columns and the table (only for an empty
test workspace; production restores a backup).
"""

import sqlalchemy as sa
from alembic import op

revision = "0159_finance_last_gaps"
down_revision = "0158_finance_depth"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
TABLE = "business_book_receipt_dismissals"
SETTINGS = "business_tax_settings"
PURGER = "anum_tenant_purger"
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("bill_id", sa.String(80), nullable=False),
        sa.Column("document_id", sa.String(80), nullable=False),
        sa.Column("dismissed_by", sa.String(160), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{TABLE}_workspace"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "bill_id", "document_id", name=f"uq_{TABLE}_pair"),
    )
    op.execute(f"alter table {TABLE} enable row level security")
    op.execute(f"alter table {TABLE} force row level security")
    op.execute(f"create policy tenant_isolation_{TABLE} on {TABLE} using ({PREDICATE}) with check ({PREDICATE})")
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {TABLE} to {PURGER}")
    op.execute("create policy lifecycle_purge_business_book_receipt_dismissals on business_book_receipt_dismissals for all to anum_tenant_purger "
               "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
               "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = business_book_receipt_dismissals.tenant_id "
               "and j.status = 'purging' and (j.kind = 'organisation_deletion' or (j.kind = 'workspace_deletion' "
               "and j.workspace_id = business_book_receipt_dismissals.workspace_id)))) "
               "with check (false)")

    op.add_column(SETTINGS, sa.Column("country", sa.String(2), nullable=True))
    op.add_column(SETTINGS, sa.Column("filing_due_days", sa.SmallInteger(), nullable=True))
    op.create_check_constraint("ck_business_tax_settings_country", SETTINGS, "country is null or country ~ '^[A-Z]{2}$'")
    op.create_check_constraint("ck_business_tax_settings_due_days", SETTINGS, "filing_due_days is null or filing_due_days between 1 and 120")


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.drop_constraint("ck_business_tax_settings_due_days", SETTINGS, type_="check")
    op.drop_constraint("ck_business_tax_settings_country", SETTINGS, type_="check")
    op.drop_column(SETTINGS, "filing_due_days")
    op.drop_column(SETTINGS, "country")
    op.execute(f"drop policy if exists lifecycle_purge_{TABLE} on {TABLE}")
    op.drop_table(TABLE)
