"""Calls & Meetings (docs/meetings.md, docs/multi-tenancy.md).

* ``meetings``: one imported call or meeting (recording, transcript file, Zoom or Teams)
  with its AI-generated notes. Workspace-shared: the forced RLS policy checks
  ``anum.tenant_id`` and ``anum.workspace_id``. A partial unique index stops the same
  Zoom/Teams transcript being imported twice in one workspace.
* ``meeting_transcript_segments``: the timed, speaker-labelled transcript lines, under
  the same workspace policy, tied to their meeting by a composite foreign key.
* ``meeting_connector_accounts``: one user's Zoom or Microsoft OAuth tokens, encrypted
  with ``ANUM_SECRETS_KEY``. Private to that user: the policy also checks
  ``anum.user_id``.

Only ``anum_maintenance`` is granted anything: the scope columns of connector accounts,
so key rotation can find them (as for developer project tokens, migration 0032).

Expand only: three new tables; the previous release ignores them.

Chained after ``0038_commerce``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0043_meetings"
down_revision = "0038_commerce"
branch_labels = None
depends_on = None

WORKSPACE_PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
USER_PREDICATE = WORKSPACE_PREDICATE + "\n          and user_id = nullif(current_setting('anum.user_id', true), '')"


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
    op.create_table(
        "meetings",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("language", sa.String(8), nullable=False, server_default="auto"),
        sa.Column("requested_language", sa.String(8), nullable=False, server_default="auto"),
        _timestamp("occurred_at", nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("agent_skill_id", sa.String(160), nullable=True),
        sa.Column("recording_file_id", sa.String(80), nullable=True),
        sa.Column("source_file_name", sa.String(255), nullable=True),
        sa.Column("external_id", sa.String(512), nullable=True),
        sa.Column("segment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(40), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("decisions", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("action_items", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("follow_up", postgresql.JSONB(), nullable=True),
        sa.Column("provenance", postgresql.JSONB(), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _workspace_fk("meetings"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "id", name="uq_meetings_scope_id"),
        sa.CheckConstraint("source in ('recording', 'transcript', 'zoom', 'teams')", name="ck_meetings_source"),
        sa.CheckConstraint(
            "status in ('queued', 'transcribing', 'analyzing', 'ready', 'needs_model', 'failed')",
            name="ck_meetings_status",
        ),
    )
    op.create_index("ix_meetings_scope_created", "meetings", ["tenant_id", "workspace_id", "created_at"])
    op.create_index(
        "uq_meetings_external",
        "meetings",
        ["tenant_id", "workspace_id", "source", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id is not null"),
    )
    _enable_rls("meetings", WORKSPACE_PREDICATE)

    op.create_table(
        "meeting_transcript_segments",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("meeting_id", sa.String(80), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.String(120), nullable=True),
        sa.Column("start_ms", sa.BigInteger(), nullable=True),
        sa.Column("end_ms", sa.BigInteger(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "meeting_id"],
            ["meetings.tenant_id", "meetings.workspace_id", "meetings.id"],
            name="fk_meeting_transcript_segments_meeting",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_meeting_segments_meeting_seq",
        "meeting_transcript_segments",
        ["tenant_id", "workspace_id", "meeting_id", "seq"],
    )
    _enable_rls("meeting_transcript_segments", WORKSPACE_PREDICATE)

    op.create_table(
        "meeting_connector_accounts",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("user_id", sa.String(160), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("account_label", sa.String(200), nullable=False),
        sa.Column("external_user_id", sa.String(200), nullable=True),
        sa.Column("scopes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("token_ciphertext", sa.Text(), nullable=False),
        _timestamp("expires_at"),
        _timestamp("connected_at"),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "user_id", "provider", name="pk_meeting_connector_accounts"),
        _workspace_fk("meeting_connector_accounts"),
        sa.CheckConstraint("provider in ('zoom', 'teams')", name="ck_meeting_connector_accounts_provider"),
    )
    _enable_rls("meeting_connector_accounts", USER_PREDICATE)
    # Key rotation (python -m anum_api.rotate_secrets) discovers connected accounts: the
    # scope columns only, never the ciphertext; it then rewrites each row as the
    # application role inside that tenant, workspace and user's RLS context.
    op.execute(
        "grant select (tenant_id, workspace_id, user_id, provider) on meeting_connector_accounts to anum_maintenance"
    )
    # Permissive policies are OR-ed per role, so this widens access for this role only.
    op.execute(
        """
        create policy maintenance_meeting_connector_tokens on meeting_connector_accounts
        for select to anum_maintenance
        using (true)
        """
    )


def downgrade() -> None:
    op.drop_table("meeting_connector_accounts")
    op.drop_table("meeting_transcript_segments")
    op.drop_table("meetings")
