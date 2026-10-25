"""Meeting room: agenda, draft decisions and action items, event log, attendance, files (F06/F07/F08).

Expand only. Chained on 0103_sales_execution; numbers were renumbered at merge
after parallel branches collided. All tables carry the scoped key, scoped foreign keys
and forced row level security like the 0098 tables.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "0104_meeting_room"
down_revision = "0103_sales_execution"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
TABLES = (
    "agent_meeting_events", "agent_meeting_agenda", "agent_meeting_outputs",
    "agent_meeting_attendance", "agent_meeting_files",
)
PREDICATE = "tenant_id = nullif(current_setting('anum.tenant_id', true), '') and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"


def _scope(table: str) -> list:
    return [
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("session_id", sa.String(80), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name=f"pk_{table}_scoped"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"],
            name=f"fk_{table}_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "session_id"],
            ["agent_meeting_sessions.tenant_id", "agent_meeting_sessions.workspace_id", "agent_meeting_sessions.id"],
            name=f"fk_{table}_session", ondelete="RESTRICT",
        ),
    ]


def _rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(f"create policy tenant_isolation_{table} on {table} using ({PREDICATE}) with check ({PREDICATE})")
    op.execute(f"grant select (tenant_id, workspace_id, ctid), delete on {table} to anum_tenant_purger")
    purge_policy = (f"create policy lifecycle_purge_{table} on {table} for all to anum_tenant_purger "  # nosec B608: fixed TABLES only
        "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
        "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = "
        f"{table}.tenant_id and j.status = 'purging' "
        "and (j.kind = 'organisation_deletion' or "
        f"(j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id)))) "
        "with check (false)")
    op.execute(purge_policy)


def upgrade() -> None:
    op.add_column("agent_meeting_sessions", sa.Column("hand_raised_by", sa.String(160)))
    op.add_column("agent_meeting_sessions", sa.Column("hand_raised_at", TS))
    op.add_column("agent_meeting_sessions", sa.Column("ended_reason", sa.String(40)))
    op.add_column("agent_meeting_sessions", sa.Column("transcript_deleted_at", TS))
    op.add_column("agent_meeting_turns", sa.Column("redacted", sa.Boolean, nullable=False, server_default=sa.false()))

    op.create_table(
        "agent_meeting_events", *_scope("agent_meeting_events"),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("actor_id", sa.String(160)),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "workspace_id", "session_id", "seq", name="uq_agent_meeting_event_seq"),
        sa.CheckConstraint("seq > 0", name="ck_agent_meeting_event_seq"),
    )
    op.create_table(
        "agent_meeting_agenda", *_scope("agent_meeting_agenda"),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("state", sa.String(10), nullable=False, server_default="open"),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "workspace_id", "session_id", "position", name="uq_agent_meeting_agenda_position"),
        sa.CheckConstraint("state in ('open', 'current', 'done')", name="ck_agent_meeting_agenda_state"),
        sa.CheckConstraint("length(btrim(title)) > 0 and position > 0", name="ck_agent_meeting_agenda_title"),
    )
    op.create_table(
        "agent_meeting_outputs", *_scope("agent_meeting_outputs"),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("body", sa.String(2000), nullable=False),
        sa.Column("state", sa.String(10), nullable=False, server_default="draft"),
        sa.Column("owner_label", sa.String(160)),
        sa.Column("source_sequences", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("task_id", sa.String(80)),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("decided_by", sa.String(160)),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind in ('decision', 'action_item')", name="ck_agent_meeting_output_kind"),
        sa.CheckConstraint("state in ('draft', 'confirmed', 'dismissed')", name="ck_agent_meeting_output_state"),
        sa.CheckConstraint("length(btrim(body)) > 0", name="ck_agent_meeting_output_body"),
        sa.CheckConstraint("task_id is null or (kind = 'action_item' and state = 'confirmed')", name="ck_agent_meeting_output_task"),
    )
    op.create_table(
        "agent_meeting_attendance", *_scope("agent_meeting_attendance"),
        sa.Column("actor_id", sa.String(160), nullable=False),
        sa.Column("joined_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("left_at", TS),
        sa.UniqueConstraint("tenant_id", "workspace_id", "session_id", "actor_id", name="uq_agent_meeting_attendance_actor"),
    )
    op.create_table(
        "agent_meeting_files", *_scope("agent_meeting_files"),
        sa.Column("file_id", sa.String(80), nullable=False),
        sa.Column("added_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "workspace_id", "session_id", "file_id", name="uq_agent_meeting_file"),
    )
    for table in TABLES:
        op.create_index(f"ix_{table}_session", table, ["tenant_id", "workspace_id", "session_id"])
        _rls(table)


def downgrade() -> None:
    # Manual operator approval, backup and a stopped feature are required for live data.
    for table in reversed(TABLES):
        op.drop_table(table)
    op.drop_column("agent_meeting_turns", "redacted")
    for column in ("transcript_deleted_at", "ended_reason", "hand_raised_at", "hand_raised_by"):
        op.drop_column("agent_meeting_sessions", column)
