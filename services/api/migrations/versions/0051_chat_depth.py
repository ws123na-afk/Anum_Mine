"""Chat depth: pinned conversations and answer ratings (docs/conversations.md).

* ``conversations.pinned_at``: nullable timestamptz. A pinned conversation leads the
  list (``PATCH /api/v1/conversations/{id}`` ``{"pinned": true}``).
* ``conversation_turns.feedback``: nullable, ``'up'`` or ``'down'``, and
  ``feedback_reason`` (at most 500 characters): the user's own thumbs on an answer
  (``PUT /api/v1/conversations/{id}/turns/{turn_id}/feedback``), stored whether or not
  learning is on.

Expand only: three nullable columns and a check constraint that accepts null, so the
previous release, which never writes or reads them, keeps working (``helm rollback``
leaves them in place). Both tables keep their forced, user-scoped row-level security;
the new columns are covered by the existing policies and table grants. The maintenance
role's column grants on ``conversations`` are unchanged: retention discovery never sees
a pin or a rating.

Chained after ``0046_home``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0051_chat_depth"
down_revision = "0046_home"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("conversation_turns", sa.Column("feedback", sa.String(8), nullable=True))
    op.add_column("conversation_turns", sa.Column("feedback_reason", sa.String(500), nullable=True))
    op.create_check_constraint(
        "ck_conversation_turns_feedback",
        "conversation_turns",
        "feedback is null or feedback in ('up', 'down')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_conversation_turns_feedback", "conversation_turns", type_="check")
    op.drop_column("conversation_turns", "feedback_reason")
    op.drop_column("conversation_turns", "feedback")
    op.drop_column("conversations", "pinned_at")
