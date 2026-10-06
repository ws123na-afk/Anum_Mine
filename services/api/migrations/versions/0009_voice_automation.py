"""Voice sessions, the automation engine, and a narrow maintenance role.

Voice sessions and transcript segments move from process memory to PostgreSQL. They are
private to the user who started them, so their RLS policies check ``anum.user_id`` as
well as the tenant and workspace.

Automation workflows, schedules and runs move from the API's local SQLite file to
PostgreSQL (``ANUM_REPOSITORY_BACKEND=postgresql``) under workspace RLS.

Three jobs must find work across tenants: the automation scheduler (which schedules are
due), the voice retention purge (which sessions' transcripts expired) and
``python -m anum_api.rotate_secrets`` (which workspaces hold an encrypted provider key).
They share the ``anum_maintenance`` role, which may only *discover* that work: it can
read a handful of id and timestamp columns of matching rows and nothing else. It cannot
read content (transcript text, workflow steps, ciphertexts) and cannot write anything.
Every read of content and every write then happens as the application role, inside the
discovered tenant's (and workspace's, and user's) RLS context. The application role's
policies are unchanged and it never bypasses RLS.

A SECURITY DEFINER function was not used: it runs as the table owner, and FORCE ROW
LEVEL SECURITY applies to the owner too, so it would need an owner exempt from row security.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_voice_automation"
down_revision = "0008_control_plane_stores"
branch_labels = None
depends_on = None

MAINTENANCE_ROLE = "anum_maintenance"

USER_TABLES = ("voice_sessions", "voice_transcript_segments")
WORKSPACE_TABLES = ("automation_workflows", "automation_schedules", "automation_runs")

WORKSPACE_PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
USER_PREDICATE = (
    WORKSPACE_PREDICATE
    + "\n          and user_id = nullif(current_setting('anum.user_id', true), '')"
)


def _timestamp(name: str, *, nullable: bool = False) -> sa.Column:
    if nullable:
        return sa.Column(name, sa.DateTime(timezone=True), nullable=True)
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"],
        ["workspaces.tenant_id", "workspaces.id"],
        name=f"fk_{table}_workspace",
    )


def _enable_rls(table: str, predicate: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_{table} on {table}
        using (
          {predicate}
        )
        with check (
          {predicate}
        )
        """
    )


def upgrade() -> None:
    # Voice ---------------------------------------------------------------------------
    op.create_table(
        "voice_sessions",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("user_id", sa.String(160), nullable=False),
        sa.Column("locale", sa.String(35), nullable=False),
        sa.Column("retention", sa.String(20), nullable=False),
        sa.Column("assistant_name", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        # Questions asked so far; incremented atomically, so the per-session limit holds
        # across API replicas.
        sa.Column("ask_count", sa.Integer(), nullable=False, server_default="0"),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        # 30-day retention: when the transcript must be gone. Null for session/permanent.
        _timestamp("expires_at", nullable=True),
        _timestamp("transcript_purged_at", nullable=True),
        _workspace_fk("voice_sessions"),
        sa.UniqueConstraint(
            "tenant_id", "workspace_id", "user_id", "id", name="uq_voice_sessions_scope_id"
        ),
        sa.CheckConstraint(
            "retention in ('session', '30_days', 'permanent')", name="ck_voice_sessions_retention"
        ),
        sa.CheckConstraint(
            "status in ('active', 'completed', 'cancelled')", name="ck_voice_sessions_status"
        ),
        sa.CheckConstraint(
            "(retention = '30_days') = (expires_at is not null)", name="ck_voice_sessions_expiry"
        ),
        sa.CheckConstraint("ask_count >= 0", name="ck_voice_sessions_ask_count"),
    )
    op.create_index(
        "ix_voice_sessions_transcript_expiry",
        "voice_sessions",
        ["expires_at"],
        postgresql_where=sa.text("expires_at is not null and transcript_purged_at is null"),
    )
    op.create_table(
        "voice_transcript_segments",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("user_id", sa.String(160), nullable=False),
        sa.Column("session_id", sa.String(80), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("is_final", sa.Boolean(), nullable=False),
        sa.Column("client_sequence", sa.Integer(), nullable=False),
        # Set once when a segment becomes a task; a second submission is refused.
        _timestamp("consumed_at", nullable=True),
        _timestamp("created_at"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "user_id", "session_id"],
            [
                "voice_sessions.tenant_id",
                "voice_sessions.workspace_id",
                "voice_sessions.user_id",
                "voice_sessions.id",
            ],
            name="fk_voice_transcript_segments_session",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("role in ('user', 'assistant')", name="ck_voice_transcript_segments_role"),
        sa.CheckConstraint("client_sequence >= 0", name="ck_voice_transcript_segments_sequence"),
    )
    # A user sequence number is used once per session; assistant replies reuse the number
    # of the question they answer.
    op.create_index(
        "uq_voice_transcript_segments_user_sequence",
        "voice_transcript_segments",
        ["session_id", "client_sequence"],
        unique=True,
        postgresql_where=sa.text("role = 'user'"),
    )
    op.create_index(
        "ix_voice_transcript_segments_session",
        "voice_transcript_segments",
        ["tenant_id", "session_id", "client_sequence"],
    )

    # Automation ----------------------------------------------------------------------
    op.create_table(
        "automation_workflows",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("steps", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _workspace_fk("automation_workflows"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "id", name="uq_automation_workflows_scope_id"),
        sa.CheckConstraint("status in ('active', 'disabled')", name="ck_automation_workflows_status"),
        sa.CheckConstraint("version >= 1", name="ck_automation_workflows_version"),
    )
    op.create_index(
        "ix_automation_workflows_scope_created",
        "automation_workflows",
        ["tenant_id", "workspace_id", "created_at"],
    )
    op.create_table(
        "automation_schedules",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("workflow_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("cron", sa.String(120), nullable=False),
        sa.Column("timezone", sa.String(80), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        # The next fire time (UTC); null while disabled. The scheduler claims due rows with
        # FOR UPDATE SKIP LOCKED and advances this in the same transaction as the run.
        _timestamp("next_run_at", nullable=True),
        _timestamp("last_run_at", nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _workspace_fk("automation_schedules"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "workflow_id"],
            [
                "automation_workflows.tenant_id",
                "automation_workflows.workspace_id",
                "automation_workflows.id",
            ],
            name="fk_automation_schedules_workflow",
        ),
    )
    op.create_index(
        "ix_automation_schedules_scope_created",
        "automation_schedules",
        ["tenant_id", "workspace_id", "created_at"],
    )
    op.create_index(
        "ix_automation_schedules_due",
        "automation_schedules",
        ["next_run_at"],
        postgresql_where=sa.text("enabled and next_run_at is not null"),
    )
    op.create_table(
        "automation_runs",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("workflow_id", sa.String(80), nullable=False),
        # The schedule that fired the run, if any (kept after the schedule is deleted).
        sa.Column("schedule_id", sa.String(80), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=True),
        sa.Column("retry_of", sa.String(80), nullable=True),
        sa.Column("current_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("steps", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _workspace_fk("automation_runs"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "workflow_id"],
            [
                "automation_workflows.tenant_id",
                "automation_workflows.workspace_id",
                "automation_workflows.id",
            ],
            name="fk_automation_runs_workflow",
        ),
        sa.CheckConstraint(
            "status in ('queued', 'running', 'paused', 'completed', 'failed', 'cancelled')",
            name="ck_automation_runs_status",
        ),
        sa.CheckConstraint("current_step >= 0", name="ck_automation_runs_current_step"),
    )
    # Idempotency keys (client keys and the scheduler's schedule:<id>:<fire time> keys)
    # make a second start of the same run impossible even if two writers race.
    op.create_index(
        "uq_automation_runs_idempotency",
        "automation_runs",
        ["tenant_id", "workspace_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key is not null"),
    )
    op.create_index(
        "ix_automation_runs_scope_created",
        "automation_runs",
        ["tenant_id", "workspace_id", "created_at"],
    )

    for table in USER_TABLES:
        _enable_rls(table, USER_PREDICATE)
    for table in WORKSPACE_TABLES:
        _enable_rls(table, WORKSPACE_PREDICATE)

    # Maintenance role ----------------------------------------------------------------
    # NOLOGIN: deployments grant it to the API login (scheduler, retention purge) and to
    # the operator login that runs rotate_secrets. When a DBA created it already, no-op.
    op.execute(
        """
        do $$
        begin
          if not exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            create role anum_maintenance nologin;
          end if;
        end
        $$
        """
    )
    op.execute("grant usage on schema public to anum_maintenance")
    # Column-level grants: ids and timestamps only. The policies below decide which rows.
    op.execute(
        "grant select (id, tenant_id, workspace_id, next_run_at) "
        "on automation_schedules to anum_maintenance"
    )
    op.execute(
        "grant select (id, tenant_id, workspace_id, user_id, expires_at) "
        "on voice_sessions to anum_maintenance"
    )
    op.execute("grant select (tenant_id, workspace_id) on workspace_model_configs to anum_maintenance")
    # Permissive policies are OR-ed per role, so these widen access for this role only.
    op.execute(
        """
        create policy maintenance_due_schedules on automation_schedules
        for select to anum_maintenance
        using (enabled and next_run_at is not null and next_run_at <= now())
        """
    )
    op.execute(
        """
        create policy maintenance_expired_voice_transcripts on voice_sessions
        for select to anum_maintenance
        using (expires_at is not null and expires_at <= now() and transcript_purged_at is null)
        """
    )
    op.execute(
        """
        create policy maintenance_encrypted_model_keys on workspace_model_configs
        for select to anum_maintenance
        using (api_key_ciphertext is not null)
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_encrypted_model_keys on workspace_model_configs")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on workspace_model_configs from anum_maintenance;
          end if;
        end
        $$
        """
    )
    for table in (
        "automation_runs",
        "automation_schedules",
        "automation_workflows",
        "voice_transcript_segments",
        "voice_sessions",
    ):
        op.drop_table(table)
    # The role itself is cluster-wide and may be granted to logins or used by other
    # databases, so it is left in place without privileges in this database.
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke usage on schema public from anum_maintenance;
          end if;
        end
        $$
        """
    )
