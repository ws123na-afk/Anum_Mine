"""Loan schedules with interest, expected one-off income and the maker and checker step for business adjustments (expand only).

docs/finance.md#lent-and-borrowed, docs/finance.md#forecast, docs/business-books.md#adjustment-approval.

* ``finance_person_loans`` gains three nullable columns: ``interest_rate`` (an annual percentage, more than 0 and at
  most 100; null means interest free), ``installments`` (1 to 360 monthly repayments) and ``first_due_on`` (the first
  repayment's date). A schedule has both ``installments`` and ``first_due_on`` or neither.
* ``finance_expected_income``: one-off money the person expects on a date (a bonus, a refund, a sale), counted by the
  forecast until it is marked received. Forced RLS like ``0157``: ``tenant_isolation_<table>``, a restrictive
  ``owner_only_<table>`` policy (also admitting the tenant purge role, which can only delete) and
  ``lifecycle_purge_<table>``. No grant to a maintenance role.
* ``business_book_settings.adjustment_approval_threshold`` (nullable): an adjustment an accountant who is not an owner
  records at or above it, in the base currency, waits as ``pending`` until another owner or accountant approves it.
  Null means no approval step.
* ``business_book_adjustments`` gains ``approved_by`` and ``approved_at``, its status check admits ``pending``, a
  pending row carries no approval, and the approver is never the maker. The frozen-row trigger is replaced: an active
  row may only become void (as before), a pending one may become active (approved, nothing else changes) or void.

Chained after ``0157_finance_evidence``.

Expand only. The previous release ignores the new columns and table; it lists only active adjustments by default and
posts only active ones, so a pending adjustment stays out of its journal too. Rollback: ``alembic downgrade
0157_finance_evidence`` deletes pending adjustments and expected income rows, restores the narrower checks and the
old trigger and drops the columns and the table (only for an empty test workspace; production restores a backup).
"""

import sqlalchemy as sa
from alembic import op

revision = "0158_finance_depth"
down_revision = "0157_finance_evidence"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
MONEY = sa.Numeric(20, 4)
LOANS = "finance_person_loans"
INCOME = "finance_expected_income"
ADJUSTMENTS = "business_book_adjustments"
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"

FROZEN = """
        create or replace function business_book_adjustments_frozen() returns trigger language plpgsql as $$
        begin
          if (new.id, new.tenant_id, new.workspace_id, new.kind, new.entry_date, new.currency, new.amount, new.section,
              new.category, new.cost_centre_id, new.project_id, new.description, new.start_month, new.months,
              new.reverse_on, new.idem_key, new.created_by, new.created_at)
             is distinct from
             (old.id, old.tenant_id, old.workspace_id, old.kind, old.entry_date, old.currency, old.amount, old.section,
              old.category, old.cost_centre_id, old.project_id, old.description, old.start_month, old.months,
              old.reverse_on, old.idem_key, old.created_by, old.created_at) then
            raise exception 'a business adjustment never changes: void it with a reason and record a new one'
              using errcode = 'check_violation';
          end if;
          if old.status = 'active' and new.status = 'void'
             and (new.approved_by, new.approved_at) is not distinct from (old.approved_by, old.approved_at) then
            return new;
          end if;
          if old.status = 'pending' and new.status = 'active' and new.approved_by is not null and new.approved_at is not null then
            return new;
          end if;
          if old.status = 'pending' and new.status = 'void' and new.approved_by is null and new.approved_at is null then
            return new;
          end if;
          raise exception 'a business adjustment never changes: void it with a reason and record a new one'
            using errcode = 'check_violation';
        end
        $$
"""

OLD_FROZEN = """
        create or replace function business_book_adjustments_frozen() returns trigger language plpgsql as $$
        begin
          if old.status <> 'active' or new.status <> 'void'
             or (new.id, new.tenant_id, new.workspace_id, new.kind, new.entry_date, new.currency, new.amount, new.section,
                 new.category, new.cost_centre_id, new.project_id, new.description, new.start_month, new.months,
                 new.reverse_on, new.idem_key, new.created_by, new.created_at)
                is distinct from
                (old.id, old.tenant_id, old.workspace_id, old.kind, old.entry_date, old.currency, old.amount, old.section,
                 old.category, old.cost_centre_id, old.project_id, old.description, old.start_month, old.months,
                 old.reverse_on, old.idem_key, old.created_by, old.created_at) then
            raise exception 'a business adjustment never changes: void it with a reason and record a new one'
              using errcode = 'check_violation';
          end if;
          return new;
        end
        $$
"""


def upgrade() -> None:
    op.add_column(LOANS, sa.Column("interest_rate", sa.Numeric(9, 4), nullable=True))
    op.add_column(LOANS, sa.Column("installments", sa.SmallInteger(), nullable=True))
    op.add_column(LOANS, sa.Column("first_due_on", sa.Date(), nullable=True))
    op.create_check_constraint("ck_finance_person_loans_interest_rate", LOANS, "interest_rate is null or (interest_rate > 0 and interest_rate <= 100)")
    op.create_check_constraint("ck_finance_person_loans_installments", LOANS, "installments is null or installments between 1 and 360")
    op.create_check_constraint("ck_finance_person_loans_schedule", LOANS, "(installments is null) = (first_due_on is null)")

    op.create_table(
        INCOME,
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("expected_on", sa.Date(), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=True),
        sa.Column("notes", sa.String(1000), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_finance_expected_income_workspace"),
        sa.CheckConstraint("amount > 0", name="ck_finance_expected_income_amount"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_finance_expected_income_currency"),
        sa.CheckConstraint("length(trim(label)) >= 1", name="ck_finance_expected_income_label"),
    )
    op.create_index("ix_finance_expected_income_owner", INCOME, ["tenant_id", "workspace_id", "owner_user_id", "expected_on"])
    op.execute(f"alter table {INCOME} enable row level security")
    op.execute(f"alter table {INCOME} force row level security")
    op.execute(f"create policy tenant_isolation_{INCOME} on {INCOME} using ({PREDICATE}) with check ({PREDICATE})")
    op.execute(f"create policy owner_only_{INCOME} on {INCOME} as restrictive "
               f"using (({OWNER_PREDICATE}) or (current_user = 'anum_tenant_purger'::name)) with check ({OWNER_PREDICATE})")
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {INCOME} to anum_tenant_purger")
    op.execute("create policy lifecycle_purge_finance_expected_income on finance_expected_income for all to anum_tenant_purger "
               "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
               "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = finance_expected_income.tenant_id and j.status = 'purging' "
               "and (j.kind = 'organisation_deletion' or (j.kind = 'workspace_deletion' and j.workspace_id = finance_expected_income.workspace_id)))) "
               "with check (false)")

    op.add_column("business_book_settings", sa.Column("adjustment_approval_threshold", sa.Numeric(24, 10), nullable=True))
    op.create_check_constraint("ck_business_book_settings_adjustment_approval", "business_book_settings",
                               "adjustment_approval_threshold is null or adjustment_approval_threshold >= 0")

    op.add_column(ADJUSTMENTS, sa.Column("approved_by", sa.String(160), nullable=True))
    op.add_column(ADJUSTMENTS, sa.Column("approved_at", TS, nullable=True))
    # A wider check: every row the old one admitted still passes.
    op.drop_constraint("ck_business_book_adjustments_status", ADJUSTMENTS, type_="check")
    op.create_check_constraint("ck_business_book_adjustments_status", ADJUSTMENTS, "status in ('active', 'void', 'pending')")
    op.create_check_constraint("ck_business_book_adjustments_pending", ADJUSTMENTS,
                               "status <> 'pending' or (approved_by is null and approved_at is null)")
    op.create_check_constraint("ck_business_book_adjustments_approval", ADJUSTMENTS,
                               "(approved_by is null) = (approved_at is null) and (approved_by is null or approved_by <> created_by)")
    op.execute(FROZEN)


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.execute(OLD_FROZEN)
    op.execute("delete from business_book_adjustments where status = 'pending'")
    op.drop_constraint("ck_business_book_adjustments_approval", ADJUSTMENTS, type_="check")
    op.drop_constraint("ck_business_book_adjustments_pending", ADJUSTMENTS, type_="check")
    op.drop_constraint("ck_business_book_adjustments_status", ADJUSTMENTS, type_="check")
    op.create_check_constraint("ck_business_book_adjustments_status", ADJUSTMENTS, "status in ('active', 'void')")
    op.drop_column(ADJUSTMENTS, "approved_at")
    op.drop_column(ADJUSTMENTS, "approved_by")
    op.drop_constraint("ck_business_book_settings_adjustment_approval", "business_book_settings", type_="check")
    op.drop_column("business_book_settings", "adjustment_approval_threshold")

    op.execute("drop policy if exists lifecycle_purge_finance_expected_income on finance_expected_income")
    op.drop_table(INCOME)

    op.drop_constraint("ck_finance_person_loans_schedule", LOANS, type_="check")
    op.drop_constraint("ck_finance_person_loans_installments", LOANS, type_="check")
    op.drop_constraint("ck_finance_person_loans_interest_rate", LOANS, type_="check")
    op.drop_column(LOANS, "first_due_on")
    op.drop_column(LOANS, "installments")
    op.drop_column(LOANS, "interest_rate")
