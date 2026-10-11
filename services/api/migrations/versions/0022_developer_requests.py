"""Developer-mode requests (docs/developer-mode.md).

``developer_requests`` holds what an owner asked the coding agent to build and what
GitHub last reported about it (workflow run, pull request, CI, merge). Rows belong to a
tenant and workspace under forced RLS; no other role is granted anything.

Expand only: one new table; the previous release ignores it.

Numbered 0022 on a parallel branch: re-chain ``down_revision`` onto the newest
migration when this lands (CLAUDE.md, parallel migrations).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022_developer_requests"
down_revision = "0021_memory_sources"
branch_labels = None
depends_on = None

TABLE = "developer_requests"
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
STATUSES = ("queued", "coding", "ci_running", "ci_failed", "waiting_approval", "merged", "failed", "cancelled")


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("allow_protected_paths", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("branch", sa.String(160), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        sa.Column("run_url", sa.String(500), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("pr_url", sa.String(500), nullable=True),
        sa.Column("pr_title", sa.String(300), nullable=True),
        sa.Column("pr_draft", sa.Boolean(), nullable=True),
        sa.Column("head_sha", sa.String(64), nullable=True),
        sa.Column("checks", postgresql.JSONB(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("merge_error", sa.Text(), nullable=True),
        sa.Column("approval_id", sa.String(80), nullable=True),
        sa.Column("merge_task_id", sa.String(80), nullable=True),
        sa.Column("merge_commit_sha", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_developer_requests_workspace",
        ),
        sa.CheckConstraint("source in ('text', 'voice')", name="ck_developer_requests_source"),
        sa.CheckConstraint(
            "status in (" + ", ".join(f"'{value}'" for value in STATUSES) + ")",
            name="ck_developer_requests_status",
        ),
    )
    # The list (newest first, keyset paging) and the daily request count.
    op.create_index(
        "ix_developer_requests_scope_created",
        TABLE,
        ["tenant_id", "workspace_id", "created_at", "id"],
    )
    op.execute(f"alter table {TABLE} enable row level security")
    op.execute(f"alter table {TABLE} force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_{TABLE} on {TABLE}
        using (
          {PREDICATE}
        )
        with check (
          {PREDICATE}
        )
        """
    )


def downgrade() -> None:
    op.drop_table(TABLE)
