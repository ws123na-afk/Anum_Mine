"""Bank and finance loan terms on a debt, and the payments accepted at once for one (expand only).

docs/finance.md#bank-loans.

* ``finance_debts`` gains twelve nullable columns, the loan's own terms: ``lender``, ``loan_start_on``, ``loan_amount``
  (what was borrowed), ``loan_rate_type`` (``reducing`` or ``flat``), ``loan_rate`` (a yearly percentage, more than 0 and at
  most 100; null means not known, never guessed), ``loan_term_months`` (1 to 600), ``loan_installment`` (the lender's
  instalment), ``loan_payment_day`` (1 to 31), ``loan_repayment`` (``bank_debit``, ``salary_deduction`` or ``manual``),
  ``loan_account_id`` (the account a bank debit leaves), ``loan_status`` (``open``, ``paid_off`` or ``closed``) and
  ``loan_closed_on``. A paid-off or closed loan has its date; a start date comes with the amount borrowed.
* ``finance_loan_batches``: the payments found for a loan that the owner accepted at once (the bank debits linked through
  the links API and the months the salary already paid), so the whole batch can be undone. Forced RLS like ``0158``:
  ``tenant_isolation_<table>``, a restrictive ``owner_only_<table>`` policy (also admitting the tenant purge role, which can
  only delete) and ``lifecycle_purge_<table>``. No grant to a maintenance role.

Chained after ``0160_finance_depth_2``.

Expand only. The previous release ignores the new columns and table: it reads a debt's typed balance as before. Rollback:
``alembic downgrade 0160_finance_depth_2`` drops the table and the columns (only for an empty test workspace; production
restores a backup, because dropping them loses the loan terms).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0161_finance_bank_loans"
down_revision = "0160_finance_depth_2"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
MONEY = sa.Numeric(20, 4)
DEBTS = "finance_debts"
BATCHES = "finance_loan_batches"
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"
COLUMNS = (
    ("lender", sa.String(120)),
    ("loan_start_on", sa.Date()),
    ("loan_amount", MONEY),
    ("loan_rate_type", sa.String(10)),
    ("loan_rate", sa.Numeric(7, 4)),
    ("loan_term_months", sa.Integer()),
    ("loan_installment", MONEY),
    ("loan_payment_day", sa.Integer()),
    ("loan_repayment", sa.String(20)),
    ("loan_account_id", sa.String(80)),
    ("loan_status", sa.String(10)),
    ("loan_closed_on", sa.Date()),
)
CHECKS = (
    ("ck_finance_debts_loan_amount", "loan_amount is null or loan_amount > 0"),
    ("ck_finance_debts_loan_rate_type", "loan_rate_type is null or loan_rate_type in ('reducing', 'flat')"),
    ("ck_finance_debts_loan_rate", "loan_rate is null or (loan_rate > 0 and loan_rate <= 100)"),
    ("ck_finance_debts_loan_term", "loan_term_months is null or loan_term_months between 1 and 600"),
    ("ck_finance_debts_loan_installment", "loan_installment is null or loan_installment > 0"),
    ("ck_finance_debts_loan_day", "loan_payment_day is null or loan_payment_day between 1 and 31"),
    ("ck_finance_debts_loan_repayment", "loan_repayment is null or loan_repayment in ('bank_debit', 'salary_deduction', 'manual')"),
    ("ck_finance_debts_loan_status", "loan_status is null or loan_status in ('open', 'paid_off', 'closed')"),
    ("ck_finance_debts_loan_closed", "(loan_status in ('paid_off', 'closed')) = (loan_closed_on is not null)"),
    ("ck_finance_debts_loan_start", "(loan_start_on is null) = (loan_amount is null)"),
)


def upgrade() -> None:
    for name, kind in COLUMNS:
        op.add_column(DEBTS, sa.Column(name, kind, nullable=True))
    for name, condition in CHECKS:
        op.create_check_constraint(name, DEBTS, condition)

    op.create_table(
        BATCHES,
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
        sa.Column("debt_id", sa.String(80), nullable=False),
        sa.Column("suggestion_key", sa.String(200), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("items", JSONB(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("undone_at", TS, nullable=True),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_finance_loan_batches_workspace"),
        sa.CheckConstraint("status in ('active', 'undone')", name="ck_finance_loan_batches_status"),
        sa.CheckConstraint("(status = 'undone') = (undone_at is not null)", name="ck_finance_loan_batches_undone"),
        sa.CheckConstraint("jsonb_typeof(items) = 'array'", name="ck_finance_loan_batches_items"),
    )
    op.create_index("ix_finance_loan_batches_owner", BATCHES, ["tenant_id", "workspace_id", "owner_user_id", "debt_id"])
    op.execute(f"alter table {BATCHES} enable row level security")
    op.execute(f"alter table {BATCHES} force row level security")
    op.execute(f"create policy tenant_isolation_{BATCHES} on {BATCHES} using ({PREDICATE}) with check ({PREDICATE})")
    op.execute(f"create policy owner_only_{BATCHES} on {BATCHES} as restrictive "
               f"using (({OWNER_PREDICATE}) or (current_user = 'anum_tenant_purger'::name)) with check ({OWNER_PREDICATE})")
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {BATCHES} to anum_tenant_purger")
    op.execute("create policy lifecycle_purge_finance_loan_batches on finance_loan_batches for all to anum_tenant_purger "
               "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
               "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = finance_loan_batches.tenant_id and j.status = 'purging' "
               "and (j.kind = 'organisation_deletion' or (j.kind = 'workspace_deletion' and j.workspace_id = finance_loan_batches.workspace_id)))) "
               "with check (false)")


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.execute("drop policy if exists lifecycle_purge_finance_loan_batches on finance_loan_batches")
    op.drop_table(BATCHES)
    for name, _condition in reversed(CHECKS):
        op.drop_constraint(name, DEBTS, type_="check")
    for name, _kind in reversed(COLUMNS):
        op.drop_column(DEBTS, name)
