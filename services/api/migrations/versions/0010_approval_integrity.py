"""Approval integrity: exact arguments, payload hash, expiry and decider.

Adds to ``approvals`` (docs/approvals-and-risk.md, threat model A1 to A3):

* ``run_id``, ``step_id``: the run and proposal step the approval belongs to.
* ``arguments``: the exact tool arguments, secret-looking values redacted for display.
* ``payload_hash``: SHA-256 (hex) of the canonical, unredacted tool call; the
  decision must send it back and the runtime re-checks it before executing.
* ``expires_at``: pending approvals lapse here and can no longer be approved.
* ``decided_by``: the user id that approved or rejected.

Rows from before this revision get ``expires_at`` 24 hours after the upgrade and no
hash, so a stale pending approval expires and an unbound one can never be approved.
The table stays under the same forced tenant/workspace RLS policy; no policy changes.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_approval_integrity"
down_revision = "0009_model_budgets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("approvals", sa.Column("run_id", sa.String(length=80), nullable=True))
    op.add_column("approvals", sa.Column("step_id", sa.String(length=80), nullable=True))
    op.add_column(
        "approvals",
        sa.Column(
            "arguments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("approvals", sa.Column("payload_hash", sa.String(length=64), nullable=True))
    # Existing rows get a 24-hour window from the upgrade. This is a DDL default rather
    # than an UPDATE so it reaches every row regardless of the forced RLS policy.
    op.add_column(
        "approvals",
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now() + interval '24 hours'"),
        ),
    )
    op.alter_column("approvals", "expires_at", server_default=None)
    op.add_column("approvals", sa.Column("decided_by", sa.String(length=160), nullable=True))
    op.create_check_constraint(
        "ck_approvals_payload_hash",
        "approvals",
        "payload_hash is null or payload_hash ~ '^[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_approvals_payload_hash", "approvals", type_="check")
    op.drop_column("approvals", "decided_by")
    op.drop_column("approvals", "expires_at")
    op.drop_column("approvals", "payload_hash")
    op.drop_column("approvals", "arguments")
    op.drop_column("approvals", "step_id")
    op.drop_column("approvals", "run_id")
