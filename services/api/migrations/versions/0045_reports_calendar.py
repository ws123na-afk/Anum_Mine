"""Typed report sections and the workspace calendar (docs/reports.md, docs/calendar.md).

* ``reports`` (migration 0042_library) gains ``tabs`` and ``sections``: typed sections
  (metrics, series, breakdown, ranked list, text) as one JSONB document, empty by default.
  The table keeps its forced RLS and owner policy.
* ``calendar_events``: the workspace's events with their attendees (a JSONB list with each
  attendee's recorded reply), meeting link and phone number, under forced RLS on tenant and
  workspace. No other role is granted anything and nothing reads across tenants.

Expand only: two nullable-free columns with defaults and one new table; the previous release
ignores them.

Chained after ``0039_team_work``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0045_reports_calendar"
down_revision = "0039_team_work"
branch_labels = None
depends_on = None

TABLES = ("calendar_events",)
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)


def _scope_columns() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
    ]


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"
    )


def upgrade() -> None:
    op.add_column("reports", sa.Column("tabs", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column(
        "reports", sa.Column("sections", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb"))
    )
    op.create_table(
        "calendar_events",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope_columns(),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("location", sa.String(200), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="scheduled"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("conference_url", sa.String(500), nullable=True),
        sa.Column("phone", sa.String(40), nullable=True),
        sa.Column("attendees", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source", sa.String(40), nullable=False, server_default="manual"),
        sa.Column("source_ref", sa.String(120), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("updated_by", sa.String(160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _workspace_fk("calendar_events"),
        sa.CheckConstraint("ends_at > starts_at", name="ck_calendar_events_times"),
        sa.CheckConstraint(
            "kind in ('meeting', 'call', 'review', 'reminder', 'focus')", name="ck_calendar_events_kind"
        ),
        sa.CheckConstraint("status in ('scheduled', 'cancelled')", name="ck_calendar_events_status"),
    )
    # Range reads (events overlapping a day or a week).
    op.create_index("ix_calendar_events_scope_starts", "calendar_events", ["tenant_id", "workspace_id", "starts_at"])
    for table in TABLES:
        op.execute(f"alter table {table} enable row level security")
        op.execute(f"alter table {table} force row level security")
        op.execute(
            f"""
            create policy tenant_isolation_{table} on {table}
            using (
              {PREDICATE}
            )
            with check (
              {PREDICATE}
            )
            """
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
    op.drop_column("reports", "sections")
    op.drop_column("reports", "tabs")
