"""Workspace lifecycle and audit log filters (docs/identity.md#workspace-lifecycle,
docs/governance-and-scale.md#audit-log).

* ``workspaces.archived_at`` / ``archived_by``: an owner may archive a workspace; it then
  refuses writes until an owner restores it. Both nullable, so the previous release
  (which never writes them) keeps working against the new schema.
* ``anum_membership_reader`` may also read ``workspaces.archived_at``, so the caller's
  workspace list (``GET /api/v1/me/workspace-memberships``) can mark archived
  workspaces. Column-level and read-only, like its other grants; its row policies are
  unchanged.
* Two indexes for the audit log's actor and action filters, newest first, built
  concurrently so a large ``audit_records`` table is not locked against inserts.

Expand only. Chained after ``0025_user_preferences``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0028_workspace_admin"
down_revision = "0027_push_devices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspaces", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("workspaces", sa.Column("archived_by", sa.String(160), nullable=True))
    op.execute("grant select (archived_at) on workspaces to anum_membership_reader")
    with op.get_context().autocommit_block():
        op.execute(
            "create index concurrently if not exists ix_audit_records_scope_actor "
            "on audit_records (tenant_id, workspace_id, actor, created_at)"
        )
        op.execute(
            "create index concurrently if not exists ix_audit_records_scope_action "
            "on audit_records (tenant_id, workspace_id, action, created_at)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("drop index concurrently if exists ix_audit_records_scope_action")
        op.execute("drop index concurrently if exists ix_audit_records_scope_actor")
    op.execute("revoke select (archived_at) on workspaces from anum_membership_reader")
    op.drop_column("workspaces", "archived_by")
    op.drop_column("workspaces", "archived_at")
