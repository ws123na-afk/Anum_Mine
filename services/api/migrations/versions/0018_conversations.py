"""Assistant conversations (docs/conversations.md).

``conversations`` and ``conversation_turns`` hold the chat the assistant answers with
its recent turns as context. Like voice sessions they are private to the user who
started them, so their forced RLS policies check ``anum.user_id`` as well as the tenant
and workspace. No other role gets access: nothing discovers conversations across
tenants.

Expand only: two new tables; the previous release ignores them.
"""

import sqlalchemy as sa
from alembic import op

revision = "0018_conversations"
down_revision = "0017_tasks_scope_created"
branch_labels = None
depends_on = None

TABLES = ("conversations", "conversation_turns")

USER_PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')\n"
    "          and user_id = nullif(current_setting('anum.user_id', true), '')"
)


def _timestamp(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("user_id", sa.String(160), nullable=False),
        sa.Column("title", sa.String(80), nullable=False, server_default=""),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_conversations_workspace",
        ),
        sa.UniqueConstraint(
            "tenant_id", "workspace_id", "user_id", "id", name="uq_conversations_scope_id"
        ),
    )
    # The conversation list: one user's most recently active conversations first.
    op.create_index(
        "ix_conversations_scope_updated",
        "conversations",
        ["tenant_id", "workspace_id", "user_id", "updated_at"],
    )
    op.create_table(
        "conversation_turns",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("user_id", sa.String(160), nullable=False),
        sa.Column("conversation_id", sa.String(80), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        _timestamp("created_at"),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "user_id", "conversation_id"],
            [
                "conversations.tenant_id",
                "conversations.workspace_id",
                "conversations.user_id",
                "conversations.id",
            ],
            name="fk_conversation_turns_conversation",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("role in ('user', 'assistant')", name="ck_conversation_turns_role"),
        sa.CheckConstraint(
            "status in ('complete', 'cancelled', 'failed')", name="ck_conversation_turns_status"
        ),
    )
    op.create_index(
        "ix_conversation_turns_conversation",
        "conversation_turns",
        ["tenant_id", "conversation_id", "seq"],
    )
    for table in TABLES:
        op.execute(f"alter table {table} enable row level security")
        op.execute(f"alter table {table} force row level security")
        op.execute(
            f"""
            create policy tenant_isolation_{table} on {table}
            using (
              {USER_PREDICATE}
            )
            with check (
              {USER_PREDICATE}
            )
            """
        )


def downgrade() -> None:
    op.drop_table("conversation_turns")
    op.drop_table("conversations")
