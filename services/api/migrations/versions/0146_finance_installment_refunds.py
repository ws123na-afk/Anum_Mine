"""Keep Tamara refund evidence and owner-applied installment refunds in the Finance planning record.

Expand only: two JSONB columns with an empty default, so the previous release keeps running against the
new schema (docs/finance.md#tamara-refunds). The existing owner-specific RLS on ``finance_plans`` applies.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0146_installment_refunds"
down_revision = "0145_finance_statement_match"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("finance_plans", sa.Column("installment_refund_notices", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("finance_plans", sa.Column("installment_refunds", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))


def downgrade() -> None:
    op.drop_column("finance_plans", "installment_refunds")
    op.drop_column("finance_plans", "installment_refund_notices")
