"""Keyset-paging index for the task list (docs/api.md, ``GET /api/v1/tasks``).

``GET /api/v1/tasks`` pages newest first with
``where (created_at, id) < (:created_at, :id) order by created_at desc, id desc limit :n``
inside one tenant/workspace scope. This index serves that query without a sort or a scan
of the workspace's whole history.

Built ``CONCURRENTLY`` (outside the migration transaction) so writes continue during the
build. Expand only: the previous release ignores the index.
"""

from alembic import op

revision = "0017_tasks_scope_created"
down_revision = "0016_retrieval_hnsw"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "create index concurrently if not exists ix_tasks_scope_created "
            "on tasks (tenant_id, workspace_id, created_at desc, id desc)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("drop index concurrently if exists ix_tasks_scope_created")
