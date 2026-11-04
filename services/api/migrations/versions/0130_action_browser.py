"""The action browser (docs/action-browser.md#storage-audit-and-retention).

New workspace tables, each under forced RLS with the tenant and workspace isolation policy of
``0095_agent_controls``:

* ``action_browser_credentials``: the owner's saved site logins and payment cards, one row per
  handle and kind. The secret parts are one Fernet value (``ANUM_SECRETS_KEY``) in
  ``secret_ciphertext``; ``hint`` is the masked form shown in lists. ``anum_maintenance`` may
  read the scope columns of rows with a ciphertext so ``rotate_secrets`` can find them
  (granted ``WITH INHERIT FALSE, SET TRUE`` by ``0009``, so it never widens the app role).
* ``action_browser_sessions``: one row per task's browser session: the task and run, the goal,
  the approved sites and handles, status, step count, the address and what it waits for.
* ``action_browser_steps``: the step log, short plain sentences with handles and masked values
  only. Pictures of pages are never stored.

Organisation and workspace deletion (``0079_tenant_lifecycle``): every new table gets the purge
grants and policy and is listed in ``anum_api/tenant_lifecycle_tables.py``.

Expand only: the previous release ignores the new tables. Rollback never reverses it. Numbered
0130 and chained after ``0129_setup_plan_gaps``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0130_action_browser"
down_revision = "0129_setup_plan_gaps"
branch_labels = None
depends_on = None

WORKSPACE_PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = ("action_browser_credentials", "action_browser_sessions", "action_browser_steps")
PURGER = "anum_tenant_purger"
JOBS = "tenant_lifecycle_jobs"
TS = sa.DateTime(timezone=True)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{value}'" for value in values) + ")"


def _force_rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_{table} on {table}
        using (
          {WORKSPACE_PREDICATE}
        )
        with check (
          {WORKSPACE_PREDICATE}
        )
        """
    )


def _purge_policy(table: str) -> None:
    covers = f"(j.kind = 'organisation_deletion' or (j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id))"
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to {PURGER}")
    op.execute(
        f"""
        create policy lifecycle_purge_{table} on {table}
        for all to {PURGER}
        using (
          tenant_id = nullif(current_setting('anum.tenant_id', true), '')
          and exists (
            select 1 from {JOBS} j
            where j.tenant_id = {table}.tenant_id and j.status = 'purging' and {covers}
          )
        )
        with check (false)
        """  # nosec B608 - fixed table names
    )


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"
    )


def upgrade() -> None:
    op.create_table(
        "action_browser_credentials",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("handle", sa.String(40), nullable=False),
        sa.Column("site", sa.String(253), nullable=True),
        sa.Column("hint", sa.String(160), nullable=False),
        sa.Column("secret_ciphertext", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name="pk_action_browser_credentials"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "kind", "handle", name="uq_action_browser_credentials_handle"),
        sa.CheckConstraint(_in("kind", ("login", "card")), name="ck_action_browser_credentials_kind"),
        sa.CheckConstraint("handle ~ '^[a-z0-9][a-z0-9-]{0,39}$'", name="ck_action_browser_credentials_handle"),
        sa.CheckConstraint("(kind = 'login') = (site is not null)", name="ck_action_browser_credentials_site"),
        sa.CheckConstraint("length(secret_ciphertext) > 0", name="ck_action_browser_credentials_secret"),
        _workspace_fk("action_browser_credentials"),
    )
    op.create_table(
        "action_browser_sessions",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("task_id", sa.String(80), nullable=False),
        sa.Column("run_id", sa.String(80), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("goal", sa.String(1000), nullable=False),
        sa.Column("start_url", sa.String(2048), nullable=False),
        sa.Column("sites", JSONB(), nullable=False),
        sa.Column("logins", JSONB(), nullable=False),
        sa.Column("cards", JSONB(), nullable=False),
        sa.Column("grants", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("browser_id", sa.String(80), nullable=True),
        sa.Column("step_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("work_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("current_url", sa.String(2048), nullable=True),
        sa.Column("current_title", sa.String(300), nullable=True),
        sa.Column("waiting", sa.String(20), nullable=True),
        sa.Column("pending", JSONB(), nullable=True),
        sa.Column("outcome", sa.String(40), nullable=True),
        sa.Column("message", sa.String(500), nullable=False, server_default=""),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name="pk_action_browser_sessions"),
        sa.CheckConstraint(
            _in("status", ("running", "waiting", "completed", "failed", "stopped")), name="ck_action_browser_sessions_status"
        ),
        sa.CheckConstraint(
            "waiting is null or " + _in("waiting", ("approval", "handoff", "off_site", "continue")), name="ck_action_browser_sessions_waiting"
        ),
        sa.CheckConstraint("step_count >= 0 and work_seconds >= 0", name="ck_action_browser_sessions_counts"),
        sa.CheckConstraint("(status in ('completed', 'failed', 'stopped')) = (ended_at is not null)", name="ck_action_browser_sessions_ended"),
        # No foreign key to tasks (its key is the id alone): the task id is checked by the API.
        _workspace_fk("action_browser_sessions"),
    )
    op.create_index("ix_action_browser_sessions_task", "action_browser_sessions", ["tenant_id", "workspace_id", "task_id", "created_at"])
    op.create_index("ix_action_browser_sessions_created", "action_browser_sessions", ["tenant_id", "workspace_id", "created_at"])
    op.create_table(
        "action_browser_steps",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("session_id", sa.String(80), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("summary", sa.String(300), nullable=False),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("detail", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "session_id", "seq", name="pk_action_browser_steps"),
        sa.CheckConstraint("seq > 0", name="ck_action_browser_steps_seq"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "session_id"],
            ["action_browser_sessions.tenant_id", "action_browser_sessions.workspace_id", "action_browser_sessions.id"],
            name="fk_action_browser_steps_session", ondelete="CASCADE",
        ),
    )
    for table in TABLES:
        _force_rls(table)
        _purge_policy(table)
    # rotate_secrets discovers which workspaces hold a ciphertext (scope columns only).
    op.execute("grant select (tenant_id, workspace_id, id) on action_browser_credentials to anum_maintenance")
    op.execute(
        """
        create policy maintenance_action_browser_credentials on action_browser_credentials
        for select to anum_maintenance
        using (true)
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_action_browser_credentials on action_browser_credentials")
    for table in reversed(TABLES):
        op.execute(f"drop policy if exists lifecycle_purge_{table} on {table}")
        op.drop_table(table)
