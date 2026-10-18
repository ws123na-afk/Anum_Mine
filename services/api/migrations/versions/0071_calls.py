"""Phone calls: contacts, the call log, AI call consent, opt-outs and the voice provider (docs/calls.md).

* ``call_contacts``: the workspace's phone contacts (name, number, company, notes,
  favourite) and, per contact, the recorded consent to be called by the AI assistant.
* ``call_log``: one row per call. Kinds: ``phone`` (the phone's own dialer, logged by the
  person), ``provider`` (placed in ANUM through the voice provider), ``ai_outbound`` and
  ``ai_inbound`` (the AI assistant on the provider number) and ``in_app`` (ANUM to ANUM
  calls, written by the in-app calling module). Notes, the AI summary and follow-up, and
  for provider calls the recording (a workspace file with an expiry) and its consent.
* ``call_opt_outs``: numbers that asked not to be called by the AI, stored only as a keyed
  hash and a masked form. Honoured before every AI call.
* ``call_provider_accounts``: one voice provider account per workspace (Twilio). The auth
  token and API key secret are Fernet ciphertext only (``credentials_ciphertext``); the
  random ``route_id`` names the workspace in webhook URLs.
* ``call_ai_requests``: an AI call someone asked for, bound to the approval that must be
  granted before it is placed.

Every table belongs to one tenant and workspace (foreign key to ``workspaces``) under forced
RLS (``tenant_isolation_<table>``). ``anum_maintenance`` is granted only what cross-tenant jobs
need to *find* work, never content: the scope and route of provider accounts (webhook routing
and key rotation, ``python -m anum_api.rotate_secrets``) and the id, scope and expiry of call
rows that hold a recording (the recording retention purge). It cannot write.

Expand only: new tables, grants and policies. The previous release ignores them.

Chained after ``0065_finance_intel`` (main's head) for now. Open pull requests hold 0066 to
0070; this revision is re-chained onto the last of them when they merge.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0071_calls"
down_revision = "0065_finance_intel"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = ("call_contacts", "call_log", "call_opt_outs", "call_provider_accounts", "call_ai_requests")


def _timestamp(name: str, *, nullable: bool = False) -> sa.Column:
    if nullable:
        return sa.Column(name, sa.DateTime(timezone=True), nullable=True)
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _scope() -> list[sa.Column]:
    return [sa.Column("tenant_id", sa.String(80), nullable=False), sa.Column("workspace_id", sa.String(80), nullable=False)]


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"
    )


def _enable_rls(table: str) -> None:
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


def upgrade() -> None:
    op.create_table(
        "call_contacts",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope(),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("phone", sa.String(40), nullable=False),
        sa.Column("phone_normalized", sa.String(40), nullable=False),
        sa.Column("company", sa.String(200), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("favourite", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("language", sa.String(8), nullable=False, server_default="auto"),
        # {granted_at, recorded_by, method, note}: the person's consent to AI calls, or null.
        sa.Column("ai_consent", postgresql.JSONB(), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _workspace_fk("call_contacts"),
        sa.CheckConstraint("source in ('manual', 'device')", name="ck_call_contacts_source"),
        sa.CheckConstraint("language in ('auto', 'en', 'ar')", name="ck_call_contacts_language"),
    )
    op.create_index("ix_call_contacts_scope_name", "call_contacts", ["tenant_id", "workspace_id", "name"])
    op.create_index("ix_call_contacts_scope_phone", "call_contacts", ["tenant_id", "workspace_id", "phone_normalized"])

    op.create_table(
        "call_log",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope(),
        sa.Column("user_id", sa.String(160), nullable=False),
        sa.Column("contact_id", sa.String(80), nullable=True),
        sa.Column("contact_name", sa.String(200), nullable=True),
        sa.Column("phone", sa.String(40), nullable=True),
        sa.Column("phone_normalized", sa.String(40), nullable=True),
        # The other ANUM user of an in-app call (claude/calls-webrtc).
        sa.Column("peer_user_id", sa.String(160), nullable=True),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=True),
        _timestamp("started_at"),
        _timestamp("ended_at", nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("follow_up", postgresql.JSONB(), nullable=True),
        sa.Column("summary_provenance", postgresql.JSONB(), nullable=True),
        sa.Column("provider", sa.String(20), nullable=True),
        sa.Column("provider_call_id", sa.String(80), nullable=True),
        sa.Column("recording_file_id", sa.String(80), nullable=True),
        sa.Column("recording_consent", postgresql.JSONB(), nullable=True),
        _timestamp("recording_expires_at", nullable=True),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("ai_request_id", sa.String(80), nullable=True),
        sa.Column("cost_cents", sa.Integer(), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _workspace_fk("call_log"),
        sa.CheckConstraint("direction in ('outbound', 'inbound')", name="ck_call_log_direction"),
        sa.CheckConstraint("kind in ('phone', 'provider', 'ai_outbound', 'ai_inbound', 'in_app')", name="ck_call_log_kind"),
        sa.CheckConstraint(
            "status in ('logged', 'dialing', 'ringing', 'in_progress', 'completed', 'failed')", name="ck_call_log_status"
        ),
        sa.CheckConstraint(
            "outcome is null or outcome in ('answered', 'no_answer', 'busy', 'failed', 'voicemail', 'missed', 'cancelled')",
            name="ck_call_log_outcome",
        ),
        sa.CheckConstraint("duration_seconds is null or duration_seconds between 0 and 86400", name="ck_call_log_duration"),
        sa.CheckConstraint("cost_cents is null or cost_cents >= 0", name="ck_call_log_cost"),
    )
    op.create_index("ix_call_log_scope_started", "call_log", ["tenant_id", "workspace_id", "started_at"])
    op.create_index("ix_call_log_scope_contact", "call_log", ["tenant_id", "workspace_id", "contact_id"])
    op.create_index(
        "uq_call_log_provider_call",
        "call_log",
        ["tenant_id", "workspace_id", "provider_call_id"],
        unique=True,
        postgresql_where=sa.text("provider_call_id is not null"),
    )

    op.create_table(
        "call_opt_outs",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope(),
        sa.Column("phone_hash", sa.String(64), nullable=False),
        sa.Column("masked", sa.String(40), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        _timestamp("created_at"),
        _workspace_fk("call_opt_outs"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "phone_hash", name="uq_call_opt_outs_phone"),
        sa.CheckConstraint("source in ('keypad', 'speech', 'owner', 'inbound')", name="ck_call_opt_outs_source"),
    )

    op.create_table(
        "call_provider_accounts",
        *_scope(),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("route_id", sa.String(40), nullable=False),
        sa.Column("account_sid", sa.String(64), nullable=False),
        sa.Column("api_key_sid", sa.String(64), nullable=True),
        sa.Column("twiml_app_sid", sa.String(64), nullable=True),
        sa.Column("from_number", sa.String(40), nullable=False),
        # Fernet ciphertext of {"auth_token", "api_key_secret"}; never a plaintext column.
        sa.Column("credentials_ciphertext", sa.Text(), nullable=False),
        sa.Column("credential_hint", sa.String(40), nullable=False),
        sa.Column("recording_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("transcription_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("recording_retention_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("ai_calls_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("inbound_ai_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ai_max_seconds", sa.Integer(), nullable=False, server_default="180"),
        sa.Column("ai_max_cost_cents", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("cost_per_minute_cents", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("business_name", sa.String(120), nullable=False),
        sa.Column("greeting", sa.String(500), nullable=True),
        sa.Column("last_check_status", sa.String(20), nullable=True),
        sa.Column("last_check_detail", sa.String(500), nullable=True),
        _timestamp("last_checked_at", nullable=True),
        sa.Column("updated_by", sa.String(160), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", name="pk_call_provider_accounts"),
        sa.UniqueConstraint("route_id", name="uq_call_provider_accounts_route"),
        _workspace_fk("call_provider_accounts"),
        sa.CheckConstraint("provider in ('twilio')", name="ck_call_provider_accounts_provider"),
        sa.CheckConstraint("recording_retention_days between 1 and 365", name="ck_call_provider_accounts_retention"),
        sa.CheckConstraint("ai_max_seconds between 30 and 900", name="ck_call_provider_accounts_ai_seconds"),
        sa.CheckConstraint("ai_max_cost_cents between 1 and 10000", name="ck_call_provider_accounts_ai_cost"),
        sa.CheckConstraint("cost_per_minute_cents between 0 and 1000", name="ck_call_provider_accounts_rate"),
    )

    op.create_table(
        "call_ai_requests",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope(),
        sa.Column("requested_by", sa.String(160), nullable=False),
        sa.Column("contact_id", sa.String(80), nullable=False),
        sa.Column("contact_name", sa.String(200), nullable=False),
        sa.Column("phone_normalized", sa.String(40), nullable=False),
        sa.Column("purpose", sa.String(500), nullable=False),
        sa.Column("script", sa.Text(), nullable=False),
        sa.Column("language", sa.String(8), nullable=False, server_default="en"),
        sa.Column("max_seconds", sa.Integer(), nullable=False),
        sa.Column("max_cost_cents", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("approval_id", sa.String(80), nullable=True),
        sa.Column("task_id", sa.String(80), nullable=True),
        sa.Column("call_id", sa.String(80), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.String(160), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _workspace_fk("call_ai_requests"),
        sa.CheckConstraint(
            "status in ('pending_approval', 'calling', 'completed', 'failed', 'cancelled')", name="ck_call_ai_requests_status"
        ),
        sa.CheckConstraint("language in ('en', 'ar')", name="ck_call_ai_requests_language"),
    )
    op.create_index("ix_call_ai_requests_scope_created", "call_ai_requests", ["tenant_id", "workspace_id", "created_at"])

    for table in TABLES:
        _enable_rls(table)

    # Webhook routing and key rotation discover provider accounts: scope and route only,
    # never a number, a SID or the ciphertext.
    op.execute("grant select (tenant_id, workspace_id, route_id) on call_provider_accounts to anum_maintenance")
    op.execute(
        """
        create policy maintenance_call_routes on call_provider_accounts
        for select to anum_maintenance
        using (true)
        """
    )
    # The recording retention purge discovers calls that still hold a recording.
    op.execute("grant select (id, tenant_id, workspace_id, recording_expires_at) on call_log to anum_maintenance")
    op.execute(
        """
        create policy maintenance_call_recordings on call_log
        for select to anum_maintenance
        using (recording_file_id is not null)
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_call_recordings on call_log")
    op.execute("drop policy if exists maintenance_call_routes on call_provider_accounts")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on call_log from anum_maintenance;
            revoke all on call_provider_accounts from anum_maintenance;
          end if;
        end
        $$
        """
    )
    for table in reversed(TABLES):
        op.drop_table(table)
