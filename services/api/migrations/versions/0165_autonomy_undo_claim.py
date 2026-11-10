"""Autonomy: an undo claims its action before the compensating tool runs (expand only).

docs/autonomy.md#activity-and-undo, docs/realtime.md#event-loop.

``autonomy_actions`` gains ``undo_claimed_at`` (timestamptz, null): when an undo claimed the
action. ``POST /autonomy/actions/{id}/undo`` used to make an undo single use by holding the
action's ``FOR UPDATE`` row lock across the compensating tool call, which kept a transaction
open across an external call (a second undo then waited on the lock for as long as the tool
took). Now the claim is committed in one short transaction, the tool runs with none open, and a
second undo sees the claim and answers 409 at once. A claim left by a process that stopped
mid-undo may be taken over after ``ANUM_INLINE_RUN_LEASE_SECONDS``.

Chained after ``0164_billing_gross_credit``.

Expand only. The previous release runs against this schema: it never reads or writes the new
column (null). Rollback: ``alembic downgrade 0164_billing_gross_credit`` drops the column; an
undo in flight at that moment is then single use by that release's row lock again.
"""

import sqlalchemy as sa
from alembic import op

revision = "0165_autonomy_undo_claim"
down_revision = "0164_billing_gross_credit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("autonomy_actions", sa.Column("undo_claimed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("autonomy_actions", "undo_claimed_at")
