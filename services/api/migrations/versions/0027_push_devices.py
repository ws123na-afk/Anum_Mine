"""Phone push notifications (docs/notifications.md).

``push_devices`` holds the phones a user registered for push in one workspace: the
platform, the Firebase Cloud Messaging token encrypted with ``ANUM_SECRETS_KEY``
(``token_ciphertext``), a SHA-256 digest of the token (so registering the same phone
again updates its row instead of adding one) and the token's last four characters (the
only part ever shown). A row is private to its user: the forced RLS policy checks
``anum.user_id`` as well as ``anum.tenant_id`` and ``anum.workspace_id``. The push sender
reads a recipient's devices inside that recipient's user context, never across users.

``notification_preferences.push_enabled`` (default true) is the per-workspace "Phone
push" switch next to the existing email and desktop switches.

``anum_maintenance`` may discover developer requests that are still in progress: the
``id``, ``tenant_id``, ``workspace_id`` and ``status`` columns of rows that are not
merged, failed or cancelled, and nothing else (no text, no write). The developer watcher
(``anum_api/push.py``) then refreshes each one from GitHub as the application role inside
its tenant and workspace, so an owner learns that a pull request is ready while the app
is closed. Grant ``anum_maintenance`` to the API login ``WITH INHERIT FALSE, SET TRUE``
(as ``infra/helm/bootstrap-database.sql`` does): the new policy admits rows of every
tenant, so an inheriting grant would widen the application role's own queries.

Expand only: one new table, one new column with a default and one new policy; the
previous release ignores all three.

Chained after ``0025_user_preferences``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0027_push_devices"
down_revision = "0026_task_archive"
branch_labels = None
depends_on = None

TABLE = "push_devices"
USER_PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')\n"
    "          and user_id = nullif(current_setting('anum.user_id', true), '')"
)
IN_PROGRESS = ("queued", "coding", "ci_running", "ci_failed", "waiting_approval")


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("user_id", sa.String(160), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("label", sa.String(80), nullable=True),
        sa.Column("token_ciphertext", sa.Text(), nullable=False),
        sa.Column("token_sha256", sa.String(64), nullable=False),
        sa.Column("token_hint", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_push_devices_workspace",
        ),
        sa.CheckConstraint("platform in ('android', 'ios')", name="ck_push_devices_platform"),
        sa.UniqueConstraint(
            "tenant_id", "workspace_id", "user_id", "token_sha256", name="uq_push_devices_user_token"
        ),
    )
    op.create_index("ix_push_devices_scope_user", TABLE, ["tenant_id", "workspace_id", "user_id", "created_at"])
    op.execute(f"alter table {TABLE} enable row level security")
    op.execute(f"alter table {TABLE} force row level security")
    op.execute(
        f"""
        create policy user_isolation_{TABLE} on {TABLE}
        using (
          {USER_PREDICATE}
        )
        with check (
          {USER_PREDICATE}
        )
        """
    )

    op.add_column(
        "notification_preferences",
        sa.Column("push_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.execute("grant select (id, tenant_id, workspace_id, status) on developer_requests to anum_maintenance")
    # Permissive policies are OR-ed per role, so this widens access for this role only.
    statuses = ", ".join(f"'{value}'" for value in IN_PROGRESS)
    op.execute(
        f"""
        create policy maintenance_developer_in_progress on developer_requests
        for select to anum_maintenance
        using (status in ({statuses}))
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_developer_in_progress on developer_requests")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on developer_requests from anum_maintenance;
          end if;
        end
        $$
        """
    )
    op.drop_column("notification_preferences", "push_enabled")
    op.drop_table(TABLE)
