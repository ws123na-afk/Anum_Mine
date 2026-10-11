"""Unified inbox: connected mailboxes, message metadata, reply drafts (docs/email.md).

* ``email_accounts``: one connected Gmail, Outlook or Zoho mailbox per row, with its
  OAuth tokens as Fernet ciphertext (``ANUM_SECRETS_KEY``), sync cursor and health.
* ``email_messages``: metadata of inbox messages (sender, recipients, subject, snippet,
  labels, thread, read/archive state) and triage. Bodies are never stored.
* ``email_drafts``: reply drafts and their send state (approval, sent, failed).
* ``email_oauth_requests``: pending connections (PKCE verifier, the returned code), short-lived.

Every table belongs to a tenant and workspace under forced RLS. ``anum_maintenance`` may
discover connected accounts for the background sync: the ``id``, ``tenant_id``,
``workspace_id``, ``connected_by``, ``status`` and ``last_sync_at`` columns of accounts
that do not need reconnecting, and nothing else (no token, no address, no write). The sync
itself runs as the application role inside each account's tenant and workspace context.
Grant ``anum_maintenance`` to the API login ``WITH INHERIT FALSE, SET TRUE`` (as
``infra/helm/bootstrap-database.sql`` does).

Expand only: four new tables; the previous release ignores them.

Chained after ``0034_autonomy``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0035_email_inbox"
down_revision = "0034_autonomy"
branch_labels = None
depends_on = None

TABLES = ("email_accounts", "email_messages", "email_drafts", "email_oauth_requests")
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
    op.create_table(
        "email_accounts",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope_columns(),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("address", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("connected_by", sa.String(160), nullable=False),
        sa.Column("visibility", sa.String(20), nullable=False, server_default="workspace"),
        sa.Column("status", sa.String(30), nullable=False, server_default="connected"),
        sa.Column("scopes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("provider_account_id", sa.String(200), nullable=True),
        sa.Column("access_token_enc", sa.Text(), nullable=True),
        sa.Column("refresh_token_enc", sa.Text(), nullable=True),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_cursor", sa.Text(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.String(20), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sync_lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _workspace_fk("email_accounts"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "provider", "address", name="uq_email_accounts_mailbox"),
        sa.CheckConstraint("provider in ('gmail', 'outlook', 'zoho')", name="ck_email_accounts_provider"),
        sa.CheckConstraint("visibility in ('workspace', 'private')", name="ck_email_accounts_visibility"),
        sa.CheckConstraint("status in ('connected', 'error', 'reauth_required')", name="ck_email_accounts_status"),
    )
    op.create_table(
        "email_messages",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope_columns(),
        sa.Column("account_id", sa.String(80), sa.ForeignKey("email_accounts.id", ondelete="CASCADE", name="fk_email_messages_account"), nullable=False),
        sa.Column("provider_message_id", sa.String(300), nullable=False),
        sa.Column("thread_id", sa.String(300), nullable=False),
        sa.Column("folder_id", sa.String(300), nullable=True),
        sa.Column("subject", sa.Text(), nullable=False, server_default=""),
        sa.Column("from_address", sa.String(320), nullable=False, server_default=""),
        sa.Column("from_name", sa.String(200), nullable=True),
        sa.Column("to_addresses", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("cc_addresses", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("snippet", sa.Text(), nullable=False, server_default=""),
        sa.Column("labels", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_attachments", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("internet_message_id", sa.String(500), nullable=True),
        sa.Column("references_header", sa.Text(), nullable=True),
        sa.Column("bulk", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("category", sa.String(30), nullable=True),
        sa.Column("priority", sa.String(10), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("triage_source", sa.String(10), nullable=True),
        sa.Column("triage_model", sa.String(160), nullable=True),
        sa.Column("triaged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _workspace_fk("email_messages"),
        sa.UniqueConstraint("account_id", "provider_message_id", name="uq_email_messages_provider_id"),
    )
    # The unified list (newest first, keyset paging) and threads.
    op.create_index("ix_email_messages_scope_received", "email_messages", ["tenant_id", "workspace_id", "received_at", "id"])
    op.create_index("ix_email_messages_thread", "email_messages", ["account_id", "thread_id"])
    op.create_table(
        "email_drafts",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope_columns(),
        sa.Column("account_id", sa.String(80), sa.ForeignKey("email_accounts.id", ondelete="CASCADE", name="fk_email_drafts_account"), nullable=False),
        sa.Column("message_id", sa.String(80), sa.ForeignKey("email_messages.id", ondelete="SET NULL", name="fk_email_drafts_message"), nullable=True),
        sa.Column("thread_id", sa.String(300), nullable=True),
        sa.Column("to_addresses", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("cc_addresses", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("subject", sa.Text(), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("model_provider", sa.String(80), nullable=True),
        sa.Column("model_name", sa.String(160), nullable=True),
        sa.Column("provider_draft_id", sa.String(300), nullable=True),
        sa.Column("approval_id", sa.String(80), nullable=True),
        sa.Column("task_id", sa.String(80), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _workspace_fk("email_drafts"),
        sa.CheckConstraint(
            "status in ('draft', 'pending_approval', 'sent', 'failed', 'discarded')", name="ck_email_drafts_status"
        ),
        sa.CheckConstraint("source in ('model', 'person')", name="ck_email_drafts_source"),
    )
    op.create_index("ix_email_drafts_message", "email_drafts", ["account_id", "message_id"])
    op.create_table(
        "email_oauth_requests",
        sa.Column("nonce", sa.String(80), primary_key=True),
        *_scope_columns(),
        sa.Column("user_id", sa.String(160), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("visibility", sa.String(20), nullable=False),
        sa.Column("code_verifier_enc", sa.Text(), nullable=True),
        sa.Column("code_enc", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _workspace_fk("email_oauth_requests"),
    )
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
    # Background sync discovery (anum_api.email_inbox.EmailSyncScheduler): ids and scope only.
    op.execute(
        "grant select (id, tenant_id, workspace_id, connected_by, status, last_sync_at) on email_accounts to anum_maintenance"
    )
    # Permissive policies are OR-ed per role, so this widens access for this role only.
    op.execute(
        """
        create policy maintenance_email_sync on email_accounts
        for select to anum_maintenance
        using (status <> 'reauth_required')
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_email_sync on email_accounts")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on email_accounts from anum_maintenance;
          end if;
        end
        $$
        """
    )
    for table in reversed(TABLES):
        op.drop_table(table)
