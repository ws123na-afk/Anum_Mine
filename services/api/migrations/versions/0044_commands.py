"""ANUM Command: command records and saved quick commands (docs/command.md).

* ``commands``: one spoken or typed request and the tasks created from its confirmed
  plan, in order (``steps``: position, task id, title, instruction, agent, tool, risk,
  approval), with the files attached to it. Status is derived from the tasks.
* ``quick_commands``: a user's saved commands and templates (Work, Personal, Custom),
  with their order and how often they were used.

Both tables are private to the user who owns the rows inside one tenant and workspace:
the forced RLS policies check ``anum.tenant_id``, ``anum.workspace_id`` and
``anum.user_id`` (like voice sessions). The tasks a command created stay ordinary
workspace tasks. No other role is granted anything.

Expand only: two new tables; the previous release ignores them.

Chained after ``0043_meetings``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0044_commands"
down_revision = "0043_meetings"
branch_labels = None
depends_on = None

TABLES = ("commands", "quick_commands")
USER_COLUMN = {"commands": "created_by", "quick_commands": "user_id"}


def _predicate(user_column: str) -> str:
    return (
        "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
        "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')\n"
        f"          and {user_column} = nullif(current_setting('anum.user_id', true), '')"
    )


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"
    )


def upgrade() -> None:
    op.create_table(
        "commands",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        # Lower-cased, whitespace-collapsed text: groups repeats for the suggestion chips.
        sa.Column("text_key", sa.String(300), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("locale", sa.String(10), nullable=False, server_default="en"),
        sa.Column("steps", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("attachments", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("quick_command_id", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _workspace_fk("commands"),
        sa.CheckConstraint(
            "source in ('typed', 'voice', 'quick_command', 'suggestion', 'next_step')", name="ck_commands_source"
        ),
    )
    # "My commands", newest first with keyset paging.
    op.create_index("ix_commands_owner_created", "commands", ["tenant_id", "workspace_id", "created_by", "created_at", "id"])
    op.create_table(
        "quick_commands",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("user_id", sa.String(160), nullable=False),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _workspace_fk("quick_commands"),
        sa.CheckConstraint("category in ('work', 'personal', 'custom')", name="ck_quick_commands_category"),
    )
    op.create_index("ix_quick_commands_owner", "quick_commands", ["tenant_id", "workspace_id", "user_id", "category", "position"])
    for table in TABLES:
        predicate = _predicate(USER_COLUMN[table])
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


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
