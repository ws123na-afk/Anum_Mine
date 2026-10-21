"""Durable, tenant-isolated live agent meeting records (ANUM MINE F06/F07).

Chained after the office registry. Does not alter imported meeting transcripts.
Explicit scoped FKs, concurrency uniqueness and forced RLS protect workspace data.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "0098_agent_live_meetings"
down_revision = "0097_office_spaces"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
TABLES = ("agent_meeting_sessions", "agent_meeting_participants", "agent_meeting_turns", "agent_meeting_turn_leases")
PREDICATE = "tenant_id = nullif(current_setting('anum.tenant_id', true), '') and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
TERMINAL = ("finished", "cancelled", "failed")


def _scope(table: str) -> list:
    return [
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name=f"pk_{table}_scoped"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"],
            name=f"fk_{table}_workspace",
        ),
    ]


def _session_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id", "session_id"],
        ["agent_meeting_sessions.tenant_id", "agent_meeting_sessions.workspace_id", "agent_meeting_sessions.id"],
        name=f"fk_{table}_session", ondelete="RESTRICT",
    )


def _rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(
        f"create policy tenant_isolation_{table} on {table} using ({PREDICATE}) with check ({PREDICATE})"
    )
    op.execute(f"grant select (tenant_id, workspace_id, ctid), delete on {table} to anum_tenant_purger")
    op.execute(
        f"""create policy lifecycle_purge_{table} on {table} for all to anum_tenant_purger  -- nosec B608: migration uses only fixed TABLES identifiers
        using (
            tenant_id = nullif(current_setting('anum.tenant_id', true), '')
            and exists (
                select 1 from tenant_lifecycle_jobs j
                where j.tenant_id = {table}.tenant_id
                  and j.status = 'purging'
                  and (j.kind = 'organisation_deletion'
                    or (j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id))
            )
        ) with check (false)"""
    )


def upgrade() -> None:
    op.create_table(
        "agent_meeting_sessions",
        *_scope("agent_meeting_sessions"),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="scheduled"),
        sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
        sa.Column("max_turns", sa.Integer, nullable=False, server_default="24"),
        sa.Column("max_duration_seconds", sa.Integer, nullable=False, server_default="1800"),
        sa.Column("max_tokens", sa.Integer, nullable=False, server_default="50000"),
        sa.Column("max_cost_usd", sa.Numeric(14, 6), nullable=False, server_default="2"),
        sa.Column("max_participants", sa.Integer, nullable=False, server_default="8"),
        sa.Column("total_input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_cost_usd", sa.Numeric(14, 6), nullable=False, server_default="0"),
        sa.Column("started_at", TS),
        sa.Column("ended_at", TS),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status in ('scheduled', 'active', 'paused', 'finished', 'cancelled', 'failed')", name="ck_agent_meeting_status"),
        sa.CheckConstraint("revision >= 1", name="ck_agent_meeting_revision"),
        sa.CheckConstraint("max_turns between 1 and 200", name="ck_agent_meeting_turn_cap"),
        sa.CheckConstraint("max_duration_seconds between 60 and 14400", name="ck_agent_meeting_duration_cap"),
        sa.CheckConstraint("max_tokens between 100 and 1000000", name="ck_agent_meeting_token_cap"),
        sa.CheckConstraint("max_cost_usd between 0 and 1000", name="ck_agent_meeting_cost_cap"),
        sa.CheckConstraint("max_participants between 1 and 50", name="ck_agent_meeting_participant_cap"),
        sa.CheckConstraint("total_input_tokens >= 0 and total_output_tokens >= 0 and total_cost_usd >= 0", name="ck_agent_meeting_usage_nonnegative"),
        sa.CheckConstraint("length(btrim(title)) > 0", name="ck_agent_meeting_title"),
    )
    op.create_table(
        "agent_meeting_participants",
        *_scope("agent_meeting_participants"),
        sa.Column("session_id", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("actor_id", sa.String(160), nullable=False),
        sa.Column("added_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        _session_fk("agent_meeting_participants"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "session_id", "actor_id", name="uq_agent_meeting_participant_actor"),
        sa.CheckConstraint("kind in ('agent', 'human')", name="ck_agent_meeting_participant_kind"),
    )
    op.create_table(
        "agent_meeting_turns",
        *_scope("agent_meeting_turns"),
        sa.Column("session_id", sa.String(80), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("speaker_id", sa.String(160), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("request_key", sa.String(120), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("estimated_cost_usd", sa.Numeric(14, 6), nullable=False, server_default="0"),
        sa.Column("provenance", sa.String(400)),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        _session_fk("agent_meeting_turns"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "session_id", "sequence", name="uq_agent_meeting_turn_sequence"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "session_id", "request_key", name="uq_agent_meeting_turn_request"),
        sa.CheckConstraint("sequence > 0", name="ck_agent_meeting_turn_sequence"),
        sa.CheckConstraint("role in ('agent', 'human', 'system')", name="ck_agent_meeting_turn_role"),
        sa.CheckConstraint("length(btrim(body)) > 0 and length(body) <= 30000", name="ck_agent_meeting_turn_body"),
        sa.CheckConstraint("input_tokens >= 0 and output_tokens >= 0 and estimated_cost_usd >= 0", name="ck_agent_meeting_turn_usage"),
    )
    op.create_table(
        "agent_meeting_turn_leases",
        *_scope("agent_meeting_turn_leases"),
        sa.Column("session_id", sa.String(80), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("agent_id", sa.String(160), nullable=False),
        sa.Column("request_key", sa.String(120), nullable=False),
        sa.Column("expected_revision", sa.Integer, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        _session_fk("agent_meeting_turn_leases"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "session_id", "sequence", name="uq_agent_meeting_lease_sequence"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "session_id", "request_key", name="uq_agent_meeting_lease_request"),
        sa.CheckConstraint("sequence > 0 and expected_revision > 0", name="ck_agent_meeting_lease_positive"),
    )
    for table in TABLES[1:]:
        op.create_index(f"ix_{table}_session", table, ["tenant_id", "workspace_id", "session_id"])
    for table in TABLES:
        _rls(table)


def downgrade() -> None:
    # Manual operator approval, backup and a stopped feature are required for live data.
    for table in reversed(TABLES):
        op.drop_table(table)
