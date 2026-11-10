"""Personal trips and projects as finance link targets (expand only).

docs/finance.md#trips-and-projects.

* ``finance_personal_tags``: a person's own trip or project (name, optional dates, the words a payment may carry, notes,
  archived). A payment is tagged to one through a finance link in the ``trip`` or ``project`` dimension (target
  ``finance_tag``); the links table already takes both dimensions, so nothing else changes. Forced RLS like ``0158``:
  ``tenant_isolation_<table>``, a restrictive ``owner_only_<table>`` policy (also admitting the tenant purge role, which can
  only delete) and ``lifecycle_purge_<table>``. No grant to a maintenance role.

A repayment split across several loans and the revaluation of foreign balances need no schema: a split is several
``person_loan`` links on one transaction (the links table's amount check already covers the parts), and revaluation is
worked out from the stored rates on every read and never stored.

Chained after ``0159_finance_last_gaps``.

Expand only. The previous release ignores the new table (its links API already accepts ``trip`` and ``project`` but has
no target registered for them, so it refuses new tags with ``target_unavailable``). Rollback: ``alembic downgrade
0159_finance_last_gaps`` drops the table (only for an empty test workspace; production restores a backup).
"""

import sqlalchemy as sa
from alembic import op

revision = "0160_finance_depth_2"
down_revision = "0159_finance_last_gaps"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
TAGS = "finance_personal_tags"
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"


def upgrade() -> None:
    op.create_table(
        TAGS,
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=True),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("keywords", sa.String(200), nullable=True),
        sa.Column("notes", sa.String(1000), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_finance_personal_tags_workspace"),
        sa.CheckConstraint("kind in ('trip', 'project')", name="ck_finance_personal_tags_kind"),
        sa.CheckConstraint("length(trim(name)) >= 1", name="ck_finance_personal_tags_name"),
        sa.CheckConstraint("starts_on is null or ends_on is null or ends_on >= starts_on", name="ck_finance_personal_tags_dates"),
    )
    op.create_index("ix_finance_personal_tags_owner", TAGS, ["tenant_id", "workspace_id", "owner_user_id", "created_at"])
    op.execute(f"alter table {TAGS} enable row level security")
    op.execute(f"alter table {TAGS} force row level security")
    op.execute(f"create policy tenant_isolation_{TAGS} on {TAGS} using ({PREDICATE}) with check ({PREDICATE})")
    op.execute(f"create policy owner_only_{TAGS} on {TAGS} as restrictive "
               f"using (({OWNER_PREDICATE}) or (current_user = 'anum_tenant_purger'::name)) with check ({OWNER_PREDICATE})")
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {TAGS} to anum_tenant_purger")
    op.execute("create policy lifecycle_purge_finance_personal_tags on finance_personal_tags for all to anum_tenant_purger "
               "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
               "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = finance_personal_tags.tenant_id and j.status = 'purging' "
               "and (j.kind = 'organisation_deletion' or (j.kind = 'workspace_deletion' and j.workspace_id = finance_personal_tags.workspace_id)))) "
               "with check (false)")


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.execute(f"drop policy if exists lifecycle_purge_{TAGS} on {TAGS}")
    op.drop_table(TAGS)
