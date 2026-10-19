"""Indexes for paged approvals (docs/approvals-and-risk.md#paging-filters-and-search).

``GET /api/v1/approvals/page`` reads a workspace's approvals newest first, pending first,
with a keyset cursor on ``(created_at, id)``; ``GET /api/v1/approvals/summary`` counts them by
status. Two indexes serve that:

* ``ix_approvals_scope_created``: ``(tenant_id, workspace_id, created_at, id)`` for the
  decided and all views and the cursor.
* ``ix_approvals_scope_pending``: the same columns for pending rows only, so the pending
  queue and its count stay cheap however long the history grows.

Both are built ``CONCURRENTLY`` so a large ``approvals`` table is never locked against
writes. Expand only: two indexes, nothing dropped or altered, no grant; the previous release
ignores them.

Numbered 0088 and chained after ``0087_email_draft_bcc``, the head of ``main`` when this was
written (renumber at merge if another migration lands first).
"""

import sqlalchemy as sa
from alembic import op

revision = "0088_approvals_paging"
down_revision = "0087_email_draft_bcc"
branch_labels = None
depends_on = None

CREATED_INDEX = "ix_approvals_scope_created"
PENDING_INDEX = "ix_approvals_scope_pending"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for index in (CREATED_INDEX, PENDING_INDEX):
            _drop_invalid(index)
        op.execute(
            f"create index concurrently if not exists {CREATED_INDEX} "
            "on approvals (tenant_id, workspace_id, created_at, id)"
        )
        op.execute(
            f"create index concurrently if not exists {PENDING_INDEX} "
            "on approvals (tenant_id, workspace_id, created_at, id) where status = 'pending'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"drop index concurrently if exists {PENDING_INDEX}")
        op.execute(f"drop index concurrently if exists {CREATED_INDEX}")


def _drop_invalid(index: str) -> None:
    """An interrupted concurrent build leaves an invalid index behind; drop it first."""
    invalid = not op.get_context().as_sql and op.get_bind().execute(
        sa.text("select not indisvalid from pg_index where indexrelid = to_regclass(:name)"),
        {"name": index},
    ).scalar()
    if invalid:
        op.execute(f"drop index concurrently if exists {index}")
