"""Sales demo workspace: the demo tag on sample records (docs/demo-workspace.md).

* ``demo_records``: one row per sample record the demo seeder created in a tenant's Demo
  workspace: the module (``kind``) and the record's id in that module. This is the demo
  tag: reset removes exactly these records, through each module's own service functions,
  and never a record somebody added to the Demo workspace by hand.

The table belongs to one tenant and workspace (foreign key to ``workspaces``) under forced
RLS: ``tenant_isolation_demo_records`` on ``anum.tenant_id`` and ``anum.workspace_id``, the
0040 pattern. A unique key keeps one tag per record.

Expand only: one new table. The previous release ignores it.

Chained after ``0075_file_scan_status`` (main's head when merged).
"""

import sqlalchemy as sa
from alembic import op

revision = "0076_demo_records"
down_revision = "0075_file_scan_status"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)


def upgrade() -> None:
    op.create_table(
        "demo_records",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("record_id", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_demo_records_workspace"
        ),
        sa.UniqueConstraint("tenant_id", "workspace_id", "kind", "record_id", name="uq_demo_records_record"),
    )
    op.execute("alter table demo_records enable row level security")
    op.execute("alter table demo_records force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_demo_records on demo_records
        using (
          {PREDICATE}
        )
        with check (
          {PREDICATE}
        )
        """
    )


def downgrade() -> None:
    op.drop_table("demo_records")
