"""Private memories remembered from conversations (docs/memory.md#from-conversations).

The assistant remembers durable facts a user states in a chat as memories with
``source_type = 'conversation'`` and ``source_id`` = the conversation id. Conversations
are private to their user (migration 0018), so the facts drawn from them are too:

* ``memories.owner_user_id``: set for a memory that only its owner may see (facts from
  that user's conversations); ``null`` for the existing workspace-shared task notes.
* ``retrieval_sources.owner_user_id``: the same owner, copied when the memory is
  indexed, so a search and the index status leave out other users' private sources.
* A ``RESTRICTIVE`` policy on each table, AND-ed with the tenant-isolation policy:
  a row with an owner is visible and writable only while ``anum.user_id`` names that
  owner. Rows without an owner are unaffected. Retrieval chunks need no policy of their
  own: every read joins their source row, and they cascade from it.

Sessions that set no ``anum.user_id`` (the retention purge, the previous release) do
not see owned rows at all, so they can neither read nor purge them; conversation
memories are kept until their owner deletes them, so nothing needs to.

Expand only: two nullable columns, two policies and a concurrently built index. The
previous release keeps working: it never writes an owner, and owned rows stay hidden
from it.
"""

import sqlalchemy as sa
from alembic import op

revision = "0021_memory_sources"
down_revision = "0020_tenants_rls"
branch_labels = None
depends_on = None

OWNER_PREDICATE = (
    "owner_user_id is null\n"
    "          or owner_user_id = nullif(current_setting('anum.user_id', true), '')"
)
TABLES = ("memories", "retrieval_sources")


def upgrade() -> None:
    op.add_column("memories", sa.Column("owner_user_id", sa.String(160), nullable=True))
    op.add_column("retrieval_sources", sa.Column("owner_user_id", sa.String(160), nullable=True))
    for table in TABLES:
        op.execute(
            f"""
            create policy owner_only_{table} on {table}
            as restrictive
            using (
              {OWNER_PREDICATE}
            )
            with check (
              {OWNER_PREDICATE}
            )
            """
        )
    # One user's conversation memories (dedupe and the Memory view).
    with op.get_context().autocommit_block():
        op.execute(
            "create index concurrently if not exists ix_memories_owner "
            "on memories (tenant_id, workspace_id, owner_user_id) where owner_user_id is not null"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("drop index concurrently if exists ix_memories_owner")
    for table in TABLES:
        op.execute(f"drop policy if exists owner_only_{table} on {table}")
    op.drop_column("retrieval_sources", "owner_user_id")
    op.drop_column("memories", "owner_user_id")
