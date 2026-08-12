from pathlib import Path

from alembic import op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "0001_foundation.sql"
    op.execute(sql_path.read_text(encoding="utf-8"))


def downgrade() -> None:
    # Child-first drops remove only objects owned by this revision. The vector
    # extension may be shared by other schemas, so this migration leaves it in place.
    op.execute("drop table if exists memories")
    op.execute("drop table if exists domain_events")
    op.execute("drop table if exists approvals")
    op.execute("drop table if exists agent_run_steps")
    op.execute("drop table if exists agent_runs")
    op.execute("drop table if exists tasks")
    op.execute("drop table if exists workspaces")
    op.execute("drop table if exists tenants")
