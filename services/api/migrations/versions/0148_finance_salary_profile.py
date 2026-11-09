"""The salary profile: one per person, next to the payslips (expand only).

docs/finance.md#salary.

* ``finance_salary_profiles``: the employer, the joining date, the expected monthly salary (gross) and its
  currency, the usual pay day, the account it is paid into, the standing rules that take money off it
  (``rules``: deductions for a loan, absence, social insurance or other, and salary advances repaid by a
  monthly deduction) and the person's decisions on the detected salary of each month (``decisions``:
  confirmed or rejected transaction ids), both JSON arrays. One row per tenant, workspace and owner.

Forced RLS like ``0147``: ``tenant_isolation_finance_salary_profiles``, a restrictive
``owner_only_finance_salary_profiles`` policy on ``owner_user_id = anum.user_id`` (also admitting the tenant purge
role, which can only delete) and ``lifecycle_purge_finance_salary_profiles``. No grant to a maintenance role.

Expand only: a new table. The previous release never reads it. Rollback: ``alembic downgrade
0147_finance_personal_money`` drops the table (only for an empty test workspace; production restores a backup).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0148_finance_salary_profile"
down_revision = "0147_finance_personal_money"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
TABLE = "finance_salary_profiles"
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
        sa.Column("employer", sa.String(120), nullable=False),
        sa.Column("joined_on", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("expected_gross", sa.Numeric(20, 4), nullable=False),
        sa.Column("pay_day", sa.SmallInteger(), nullable=False),
        sa.Column("account_id", sa.String(80), nullable=True),
        sa.Column("rules", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("decisions", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_finance_salary_profiles_workspace"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", name="uq_finance_salary_profiles_owner"),
        sa.CheckConstraint("expected_gross >= 0", name="ck_finance_salary_profiles_gross"),
        sa.CheckConstraint("pay_day between 1 and 31", name="ck_finance_salary_profiles_pay_day"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_finance_salary_profiles_currency"),
        sa.CheckConstraint("jsonb_typeof(rules) = 'array' and jsonb_typeof(decisions) = 'array'", name="ck_finance_salary_profiles_json"),
    )
    op.execute(f"alter table {TABLE} enable row level security")
    op.execute(f"alter table {TABLE} force row level security")
    op.execute(f"create policy tenant_isolation_{TABLE} on {TABLE} using ({PREDICATE}) with check ({PREDICATE})")
    op.execute(f"create policy owner_only_{TABLE} on {TABLE} as restrictive "
               f"using (({OWNER_PREDICATE}) or (current_user = 'anum_tenant_purger'::name)) with check ({OWNER_PREDICATE})")
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {TABLE} to anum_tenant_purger")
    op.execute("create policy lifecycle_purge_finance_salary_profiles on finance_salary_profiles for all to anum_tenant_purger "
               "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
               "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = finance_salary_profiles.tenant_id and j.status = 'purging' "
               "and (j.kind = 'organisation_deletion' or (j.kind = 'workspace_deletion' and j.workspace_id = finance_salary_profiles.workspace_id)))) "
               "with check (false)")


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.execute(f"drop policy if exists lifecycle_purge_{TABLE} on {TABLE}")
    op.drop_table(TABLE)
