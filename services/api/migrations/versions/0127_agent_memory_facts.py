"""Agent working memory: kind, source, date, edits and retention (docs/agents.md#agent-memories).

Nullable columns on ``agent_memories`` (migration ``0041_agent_profiles``):

* ``kind``: ``fact`` (a recurring fact) or ``decision`` (a decision the owner or a person made).
  Null reads as ``fact``.
* ``source_label`` and ``source_date``: where the memory comes from (for example "SOP: tyre
  rotation, version 3") and the date the fact or decision holds from.
* ``source_file_id``: a Documents file the memory was taken from; the API only accepts a file
  the agent itself may read (its knowledge sources) and the person saving it can see.
* ``updated_at`` and ``updated_by``: the last edit (``PATCH``), null for a memory never edited.
* ``expires_at``: set from the organisation's memory governance (``default_retention_days``)
  when permanent retention is not allowed; null keeps it until someone deletes it. Reads hide
  an expired memory at once and the next change to that agent's memories deletes it.

The source check widens from ``manual, task`` to ``manual, task, file``: every row the previous
release wrote still passes, and the previous release never writes ``file``. Check constraints
on ``kind`` and the column lengths are new. Forced row level security is unchanged (no new
table, no new grant), so ``tenant_lifecycle_tables.py`` needs no entry. Nothing is backfilled.

Expand only: the previous release reads and writes the table without the new columns. Rollback
never reverses it.

Numbered 0127 and chained after ``0126_business_invoice_send``, the head of ``main``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0127_agent_memory_facts"
down_revision = "0126_business_invoice_send"
branch_labels = None
depends_on = None

TABLE = "agent_memories"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("kind", sa.String(20), nullable=True))
    op.add_column(TABLE, sa.Column("source_label", sa.String(200), nullable=True))
    op.add_column(TABLE, sa.Column("source_date", sa.Date(), nullable=True))
    op.add_column(TABLE, sa.Column("source_file_id", sa.String(80), nullable=True))
    op.add_column(TABLE, sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(TABLE, sa.Column("updated_by", sa.String(160), nullable=True))
    op.add_column(TABLE, sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("ck_agent_memories_kind", TABLE, "kind is null or kind in ('fact', 'decision')")
    # Widened in one statement pair inside the migration transaction: no moment without a check.
    op.drop_constraint("ck_agent_memories_source", TABLE, type_="check")
    op.create_check_constraint("ck_agent_memories_source", TABLE, "source in ('manual', 'task', 'file')")
    op.create_check_constraint(
        "ck_agent_memories_file_source", TABLE, "source <> 'file' or source_file_id is not null"
    )


def downgrade() -> None:
    op.drop_constraint("ck_agent_memories_file_source", TABLE, type_="check")
    op.drop_constraint("ck_agent_memories_source", TABLE, type_="check")
    op.execute("delete from agent_memories where source = 'file'")
    op.create_check_constraint("ck_agent_memories_source", TABLE, "source in ('manual', 'task')")
    op.drop_constraint("ck_agent_memories_kind", TABLE, type_="check")
    for column in ("expires_at", "updated_by", "updated_at", "source_file_id", "source_date", "source_label", "kind"):
        op.drop_column(TABLE, column)
