"""User-built agents (docs/agents.md, docs/multi-tenancy.md).

- ``agent_profiles``: one row per agent in a workspace. Forced RLS on the tenant and
  workspace, and either ``sharing = 'workspace'`` or the owner's ``anum.user_id``: a
  private agent is invisible (and unwritable) to everyone but its owner.
- ``agent_memories``: memories that belong to one agent. Forced RLS on the tenant and
  workspace and on its agent being visible (the policy reads ``agent_profiles``, whose
  own policy applies), so an agent's memories are exactly as private as the agent.
  Deleting an agent deletes its memories (``on delete cascade``).
- ``tasks.agent_id`` and ``conversations.agent_id`` (nullable): the agent a task runs
  as and the agent a chat thread is with. No foreign key: a deleted agent's tasks and
  chats stay in the history. Indexed (concurrently) for "recent tasks" per agent.

No role is granted anything new and nothing bypasses RLS: nothing reads agents across
tenants.

Expand only: two new tables and two nullable columns. The previous release ignores
them (it neither writes ``agent_id`` nor reads the new tables).

Chained after ``0048_task_planning``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0041_agent_profiles"
down_revision = "0048_task_planning"
branch_labels = None
depends_on = None

SCOPE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
PROFILE_PREDICATE = (
    f"{SCOPE}\n"
    "          and (sharing = 'workspace'\n"
    "               or owner_user_id = nullif(current_setting('anum.user_id', true), ''))"
)
MEMORY_PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')\n"
    "          and exists (\n"
    "            select 1 from agent_profiles a\n"
    "            where a.tenant_id = agent_memories.tenant_id\n"
    "              and a.workspace_id = agent_memories.workspace_id\n"
    "              and a.id = agent_memories.agent_id\n"
    "          )"
)


def _force_rls(table: str, predicate: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(
        f"""
        create policy scope_isolation_{table} on {table}
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
        "agent_profiles",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("icon", sa.String(40), nullable=False),
        sa.Column("color", sa.String(7), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("department", sa.String(80), nullable=True),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("instructions", sa.Text(), nullable=False, server_default=""),
        sa.Column("model_route", sa.String(20), nullable=False),
        sa.Column("allowed_skills", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("allowed_tools", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("autonomy", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("knowledge_mode", sa.String(20), nullable=False),
        sa.Column("knowledge_sources", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("sharing", sa.String(20), nullable=False),
        sa.Column("template_id", sa.String(40), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_agent_profiles_workspace",
        ),
        sa.UniqueConstraint("tenant_id", "workspace_id", "id", name="uq_agent_profiles_scope_id"),
        sa.CheckConstraint("category in ('work', 'personal', 'custom')", name="ck_agent_profiles_category"),
        sa.CheckConstraint("status in ('active', 'paused')", name="ck_agent_profiles_status"),
        sa.CheckConstraint("sharing in ('private', 'workspace')", name="ck_agent_profiles_sharing"),
        sa.CheckConstraint("model_route in ('fast', 'capable')", name="ck_agent_profiles_model_route"),
        sa.CheckConstraint(
            "knowledge_mode in ('none', 'workspace', 'selected')", name="ck_agent_profiles_knowledge_mode"
        ),
    )
    op.create_index(
        "ix_agent_profiles_scope_updated",
        "agent_profiles",
        ["tenant_id", "workspace_id", sa.text("updated_at DESC")],
    )
    _force_rls("agent_profiles", PROFILE_PREDICATE)

    op.create_table(
        "agent_memories",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("agent_id", sa.String(80), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("task_id", sa.String(80), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "agent_id"],
            ["agent_profiles.tenant_id", "agent_profiles.workspace_id", "agent_profiles.id"],
            name="fk_agent_memories_agent",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("source in ('manual', 'task')", name="ck_agent_memories_source"),
    )
    op.create_index(
        "ix_agent_memories_agent",
        "agent_memories",
        ["tenant_id", "workspace_id", "agent_id", sa.text("created_at DESC")],
    )
    _force_rls("agent_memories", MEMORY_PREDICATE)

    op.add_column("tasks", sa.Column("agent_id", sa.String(80), nullable=True))
    op.add_column("conversations", sa.Column("agent_id", sa.String(80), nullable=True))
    with op.get_context().autocommit_block():
        op.execute(
            "create index concurrently if not exists ix_tasks_scope_agent "
            "on tasks (tenant_id, workspace_id, agent_id, created_at desc, id desc) "
            "where agent_id is not null"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("drop index concurrently if exists ix_tasks_scope_agent")
    op.drop_column("conversations", "agent_id")
    op.drop_column("tasks", "agent_id")
    op.drop_table("agent_memories")
    op.drop_table("agent_profiles")
