"""Home: pinned apps, quick commands, insight dismissals and the greeting name (docs/home.md).

* ``home_profiles``: the name Home greets a user by, one row per tenant and user.
* ``home_pins``: a user's pinned apps in one workspace (another workspace, an https link
  to one of the owner's apps, or an ANUM feature), in the user's order.
* ``home_commands``: a user's own quick commands (Work, Personal or Custom).
* ``home_insight_states``: which proactive insights a user dismissed or snoozed, keyed by
  the insight's stable key (``<source>:<id>``).

Every row is private to its user: the forced RLS policies check ``anum.user_id`` as well
as ``anum.tenant_id`` (and ``anum.workspace_id`` for the workspace tables). No other role
is granted anything: nothing reads Home data across users or tenants.

Expand only: four new tables; the previous release ignores them.

Chained after ``0035_email_inbox``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0036_home"
down_revision = "0035_email_inbox"
branch_labels = None
depends_on = None

USER_PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and user_id = nullif(current_setting('anum.user_id', true), '')"
)
WORKSPACE_USER_PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')\n"
    "          and user_id = nullif(current_setting('anum.user_id', true), '')"
)
WORKSPACE_TABLES = ("home_pins", "home_commands", "home_insight_states")


def _scope_columns() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("user_id", sa.String(160), nullable=False),
    ]


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"
    )


def _force_rls(table: str, predicate: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(
        f"""
        create policy user_isolation_{table} on {table}
        using (
          {predicate}
        )
        with check (
          {predicate}
        )
        """
    )


def upgrade() -> None:
    op.create_table(
        "home_profiles",
        sa.Column("tenant_id", sa.String(80), sa.ForeignKey("tenants.id", name="fk_home_profiles_tenant"), nullable=False),
        sa.Column("user_id", sa.String(160), nullable=False),
        sa.Column("display_name", sa.String(80), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "user_id", name="pk_home_profiles"),
    )
    op.create_table(
        "home_pins",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope_columns(),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("label", sa.String(60), nullable=False),
        sa.Column("target", sa.String(500), nullable=False),
        sa.Column("icon", sa.String(40), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _workspace_fk("home_pins"),
        sa.CheckConstraint("kind in ('workspace', 'link', 'feature')", name="ck_home_pins_kind"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "user_id", "kind", "target", name="uq_home_pins_target"),
    )
    op.create_index("ix_home_pins_user", "home_pins", ["tenant_id", "workspace_id", "user_id", "position"])
    op.create_table(
        "home_commands",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope_columns(),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("label", sa.String(60), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _workspace_fk("home_commands"),
        sa.CheckConstraint("mode in ('work', 'personal', 'custom')", name="ck_home_commands_mode"),
    )
    op.create_index("ix_home_commands_user", "home_commands", ["tenant_id", "workspace_id", "user_id", "position"])
    op.create_table(
        "home_insight_states",
        *_scope_columns(),
        sa.Column("insight_key", sa.String(200), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "user_id", "insight_key", name="pk_home_insight_states"),
        _workspace_fk("home_insight_states"),
        sa.CheckConstraint("state in ('dismissed', 'snoozed')", name="ck_home_insight_states_state"),
    )
    _force_rls("home_profiles", USER_PREDICATE)
    for table in WORKSPACE_TABLES:
        _force_rls(table, WORKSPACE_USER_PREDICATE)


def downgrade() -> None:
    for table in ("home_insight_states", "home_commands", "home_pins", "home_profiles"):
        op.drop_table(table)
