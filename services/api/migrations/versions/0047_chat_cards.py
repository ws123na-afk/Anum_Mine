"""Cards on assistant turns: tool use in chat (docs/conversations.md#tools-in-chat).

* ``conversation_turns.card``: nullable JSONB. When a chat question needs workspace data
  or an action it runs as a task through the runtime, and the assistant turn keeps the
  outcome (results with links, the pending approval with its payload hash, or the
  failure) so the conversation shows it again when reopened.

Expand only: the previous release never writes or reads the column. The table keeps its
forced, user-scoped row-level security; the new column is covered by the existing
policies and table grants (the maintenance role's column grants are on
``conversations`` only and are unchanged).

Chained after ``0039_team_work``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0047_chat_cards"
down_revision = "0040_marketing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversation_turns", sa.Column("card", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("conversation_turns", "card")
