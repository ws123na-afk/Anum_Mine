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
    op.execute("drop table if exists memories cascade")
    op.execute("drop table if exists domain_events cascade")
    op.execute("drop table if exists approvals cascade")
    op.execute("drop table if exists agent_run_steps cascade")
    op.execute("drop table if exists agent_runs cascade")
    op.execute("drop table if exists tasks cascade")
    op.execute("drop table if exists workspaces cascade")
    op.execute("drop table if exists tenants cascade")
