"""PDF statement layouts the owner taught (expand only). docs/finance-statements.md#templates.

* ``finance_statement_templates``: one row per bank layout a person corrected in the PDF statement
  preview: the layout's ``fingerprint`` (a hash of the header words and where the columns sit, nothing
  personal), the header labels, the role the owner gave each column and the date format. The next
  statement with the same fingerprint is read with it. Unique per person and fingerprint.

Forced RLS like ``0131``: ``tenant_isolation_<table>``, a restrictive ``owner_only_<table>`` policy on
``owner_user_id = anum.user_id`` (also admitting the tenant purge role, which can only delete) and
``lifecycle_purge_<table>``.

Expand only: a new table the previous release ignores. Chained after ``0138_books_v2``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0139_finance_pdf_templates"
down_revision = "0138_books_v2"
branch_labels = None
depends_on = None

TABLE = "finance_statement_templates"
TS = sa.DateTime(timezone=True)
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
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("labels", postgresql.JSONB(), nullable=False),
        sa.Column("roles", postgresql.JSONB(), nullable=False),
        sa.Column("date_format", sa.String(20), nullable=True),
        sa.Column("uses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", TS, nullable=True),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{TABLE}_workspace"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "fingerprint", name=f"uq_{TABLE}_owner_fingerprint"),
        sa.CheckConstraint("fingerprint ~ '^[0-9a-f]{32}$'", name=f"ck_{TABLE}_fingerprint"),
        sa.CheckConstraint("jsonb_typeof(labels) = 'array' and jsonb_array_length(labels) <= 12", name=f"ck_{TABLE}_labels"),
        sa.CheckConstraint("jsonb_typeof(roles) = 'array' and jsonb_array_length(roles) <= 12", name=f"ck_{TABLE}_roles"),
    )
    op.execute(f"alter table {TABLE} enable row level security")
    op.execute(f"alter table {TABLE} force row level security")
    op.execute(f"create policy tenant_isolation_{TABLE} on {TABLE} using ({PREDICATE}) with check ({PREDICATE})")
    op.execute(f"create policy owner_only_{TABLE} on {TABLE} as restrictive "
               f"using (({OWNER_PREDICATE}) or (current_user = 'anum_tenant_purger'::name)) with check ({OWNER_PREDICATE})")
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {TABLE} to anum_tenant_purger")
    purge_policy = (f"create policy lifecycle_purge_{TABLE} on {TABLE} for all to anum_tenant_purger "  # nosec B608: fixed table name
        "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
        "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = "
        f"{TABLE}.tenant_id and j.status = 'purging' "
        "and (j.kind = 'organisation_deletion' or "
        f"(j.kind = 'workspace_deletion' and j.workspace_id = {TABLE}.workspace_id)))) "
        "with check (false)")
    op.execute(purge_policy)


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.execute(f"drop policy if exists lifecycle_purge_{TABLE} on {TABLE}")
    op.drop_table(TABLE)
