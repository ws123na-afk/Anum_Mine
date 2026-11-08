"""Store complete importable installment statement rows in the owner's Finance planning record.

Paid here means paid as reported by the source, not a matched bank transaction.
All existing Finance plan RLS policies continue to apply.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0144_installment_stmt"
down_revision = "0143_finance_money_advisor"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("finance_plans", sa.Column("installment_entries", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))

def downgrade() -> None:
    op.drop_column("finance_plans", "installment_entries")
