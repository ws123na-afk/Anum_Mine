"""Blind copies on email inbox drafts (docs/email.md#composing-a-new-message).

``email_drafts`` gains ``bcc_addresses``: the blind copy recipients of a new message or a
reply, part of the ``send_email`` call the owner approves (the payload hash covers them).

Expand only: one JSONB column, not null with a server default of an empty list, so rows the
previous release inserts (which never name the column) get ``[]``. No grant, no policy: the
table's forced RLS already covers the new column.

Numbered 0087 and chained after ``0080_business_change_orders``, the head of ``main``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0087_email_draft_bcc"
down_revision = "0080_business_change_orders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "email_drafts",
        sa.Column("bcc_addresses", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("email_drafts", "bcc_addresses")
