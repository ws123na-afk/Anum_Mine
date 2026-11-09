"""Personal money: lent and borrowed with people, payslips, remittances, goal purpose (expand only).

docs/finance.md#lent-and-borrowed, #payslips, #sending-money-home and #debts-and-goals.

* ``finance_person_loans``: an interest-free loan with a person (``direction`` ``lent`` or ``borrowed``), the
  amount and currency, dates, notes and the in-app reminder switches (``reminded_for`` and
  ``reminded_overdue_for`` record the due date already announced, so each is sent once).
* ``finance_payslips``: one payslip: employer, pay month, gross and the deductions (JSON list of kind,
  amount, label).
* ``finance_remittances``: money sent home: amount, fee, rate, what arrived and in which currency, recipient,
  provider and whether it is sent every month.
* ``finance_goals.purpose`` (default ``general``): what a goal is for (emergency fund, family at home, a
  purchase, education, travel).
* ``finance_links`` admits three more dimensions: ``person_loan``, ``payslip`` and ``remittance``.

The three tables: forced RLS like ``0143``: ``tenant_isolation_<table>``, a restrictive ``owner_only_<table>``
policy on ``owner_user_id = anum.user_id`` (also admitting the tenant purge role, which can only delete) and
``lifecycle_purge_<table>``. The loan reminders run as the application role in each person's own scope after
the advisor's discovery (``finance_accounts`` scope columns, migration 0143); no new grant to a maintenance role.

Expand only: new tables, a nullable-safe column with a default and a widened check. The previous release
ignores them and never writes the new link dimensions. Chained after ``0146_installment_refunds`` (Tamara refunds, #412).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0147_finance_personal_money"
down_revision = "0146_installment_refunds"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
MONEY = sa.Numeric(20, 4)
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"
LOANS = "finance_person_loans"
PAYSLIPS = "finance_payslips"
REMITTANCES = "finance_remittances"
TABLES = (LOANS, PAYSLIPS, REMITTANCES)
CURRENCY = "~ '^[A-Z]{3}$'"
# ``transfer`` came with 0145_finance_statement_match (own transfers), which this chain follows on main.
OLD = "dimension in ('vehicle', 'purchase', 'document', 'business', 'trip', 'project', 'bill', 'debt', 'transfer')"
NEW = ("dimension in ('vehicle', 'purchase', 'document', 'business', 'trip', 'project', 'bill', 'debt', 'transfer', 'person_loan', 'payslip', "
       "'remittance')")
PURPOSES = "purpose in ('general', 'emergency', 'family', 'purchase', 'education', 'travel')"


def _scope() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
    ]


def _rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(f"create policy tenant_isolation_{table} on {table} using ({PREDICATE}) with check ({PREDICATE})")
    op.execute(f"create policy owner_only_{table} on {table} as restrictive "
               f"using (({OWNER_PREDICATE}) or (current_user = 'anum_tenant_purger'::name)) with check ({OWNER_PREDICATE})")
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to anum_tenant_purger")


def upgrade() -> None:
    op.create_table(
        LOANS,
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope(),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("person", sa.String(120), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("lent_on", sa.Date(), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("notes", sa.String(1000), nullable=True),
        sa.Column("remind", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("remind_days_before", sa.SmallInteger(), nullable=False, server_default="3"),
        sa.Column("reminded_for", sa.Date(), nullable=True),
        sa.Column("reminded_overdue_for", sa.Date(), nullable=True),
        sa.Column("closed_on", sa.Date(), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_finance_person_loans_workspace"),
        sa.CheckConstraint("direction in ('lent', 'borrowed')", name="ck_finance_person_loans_direction"),
        sa.CheckConstraint("amount > 0", name="ck_finance_person_loans_amount"),
        sa.CheckConstraint(f"currency {CURRENCY}", name="ck_finance_person_loans_currency"),
        sa.CheckConstraint("remind_days_before between 0 and 30", name="ck_finance_person_loans_remind"),
    )
    op.create_index("ix_finance_person_loans_owner", LOANS, ["tenant_id", "workspace_id", "owner_user_id", "lent_on"])
    op.create_table(
        PAYSLIPS,
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope(),
        sa.Column("employer", sa.String(120), nullable=False),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("gross", MONEY, nullable=False),
        sa.Column("deductions", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("notes", sa.String(1000), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_finance_payslips_workspace"),
        sa.CheckConstraint("month ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'", name="ck_finance_payslips_month"),
        sa.CheckConstraint("gross >= 0", name="ck_finance_payslips_gross"),
        sa.CheckConstraint(f"currency {CURRENCY}", name="ck_finance_payslips_currency"),
        sa.CheckConstraint("jsonb_typeof(deductions) = 'array'", name="ck_finance_payslips_deductions"),
    )
    op.create_index("ix_finance_payslips_owner", PAYSLIPS, ["tenant_id", "workspace_id", "owner_user_id", "month"])
    op.create_table(
        REMITTANCES,
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope(),
        sa.Column("sent_on", sa.Date(), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("fee", MONEY, nullable=False, server_default="0"),
        sa.Column("fx_rate", sa.Numeric(20, 8), nullable=True),
        sa.Column("received_amount", MONEY, nullable=True),
        sa.Column("received_currency", sa.String(3), nullable=True),
        sa.Column("recipient", sa.String(120), nullable=False),
        sa.Column("provider", sa.String(120), nullable=False),
        sa.Column("regular", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.String(1000), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_finance_remittances_workspace"),
        sa.CheckConstraint("amount > 0 and fee >= 0", name="ck_finance_remittances_amounts"),
        sa.CheckConstraint(f"currency {CURRENCY}", name="ck_finance_remittances_currency"),
        sa.CheckConstraint(f"received_currency is null or received_currency {CURRENCY}", name="ck_finance_remittances_received_currency"),
        sa.CheckConstraint("fx_rate is null or fx_rate > 0", name="ck_finance_remittances_rate"),
    )
    op.create_index("ix_finance_remittances_owner", REMITTANCES, ["tenant_id", "workspace_id", "owner_user_id", "sent_on"])
    for table in TABLES:
        _rls(table)
    op.execute("create policy lifecycle_purge_finance_person_loans on finance_person_loans for all to anum_tenant_purger "
               "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
               "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = finance_person_loans.tenant_id and j.status = 'purging' "
               "and (j.kind = 'organisation_deletion' or (j.kind = 'workspace_deletion' and j.workspace_id = finance_person_loans.workspace_id)))) "
               "with check (false)")
    op.execute("create policy lifecycle_purge_finance_payslips on finance_payslips for all to anum_tenant_purger "
               "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
               "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = finance_payslips.tenant_id and j.status = 'purging' "
               "and (j.kind = 'organisation_deletion' or (j.kind = 'workspace_deletion' and j.workspace_id = finance_payslips.workspace_id)))) "
               "with check (false)")
    op.execute("create policy lifecycle_purge_finance_remittances on finance_remittances for all to anum_tenant_purger "
               "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
               "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = finance_remittances.tenant_id and j.status = 'purging' "
               "and (j.kind = 'organisation_deletion' or (j.kind = 'workspace_deletion' and j.workspace_id = finance_remittances.workspace_id)))) "
               "with check (false)")
    op.add_column("finance_goals", sa.Column("purpose", sa.String(20), nullable=False, server_default="general"))
    op.create_check_constraint("ck_finance_goals_purpose", "finance_goals", PURPOSES)
    op.drop_constraint("ck_finance_links_dimension", "finance_links", type_="check")
    op.create_check_constraint("ck_finance_links_dimension", "finance_links", NEW)


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.execute("delete from finance_links where dimension in ('person_loan', 'payslip', 'remittance')")
    op.drop_constraint("ck_finance_links_dimension", "finance_links", type_="check")
    op.create_check_constraint("ck_finance_links_dimension", "finance_links", OLD)
    op.drop_constraint("ck_finance_goals_purpose", "finance_goals", type_="check")
    op.drop_column("finance_goals", "purpose")
    for table in reversed(TABLES):
        op.execute(f"drop policy if exists lifecycle_purge_{table} on {table}")
        op.drop_table(table)
