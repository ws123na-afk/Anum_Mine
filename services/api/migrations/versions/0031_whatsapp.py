"""WhatsApp inbox (docs/whatsapp.md).

* ``whatsapp_accounts``: one WhatsApp Cloud API account per workspace. The access token
  and the app secret are stored only as Fernet ciphertext (``ANUM_SECRETS_KEY``), the
  webhook verify token only as its SHA-256. ``phone_number_id`` is unique across all
  tenants, so an inbound webhook maps to exactly one workspace.
* ``whatsapp_contacts``, ``whatsapp_conversations``, ``whatsapp_messages``: the
  workspace's customers, one conversation per customer, and every message in and out
  (media as metadata only: bytes are fetched from Meta on demand).
  ``uq_whatsapp_messages_wamid`` makes inbound processing idempotent.

All four tables are under forced RLS on ``anum.tenant_id`` and ``anum.workspace_id``.

Webhook routing (docs/multi-tenancy.md#maintenance-role): the webhook carries no tenant,
so it discovers the owning workspace as ``anum_maintenance``. That role may read only
``tenant_id``, ``workspace_id``, ``phone_number_id`` and ``verify_token_hash`` of
``whatsapp_accounts`` (column grants) and nothing else; it cannot write. Everything
after the lookup runs as the application role inside that workspace's RLS context.

Chained after ``0030_integration_health``.

Expand only: four new tables, grants and policies; the previous release ignores them.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0031_whatsapp"
down_revision = "0030_integration_health"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = ("whatsapp_accounts", "whatsapp_contacts", "whatsapp_conversations", "whatsapp_messages")
MESSAGE_STATUSES = ("received", "awaiting_approval", "sending", "sent", "delivered", "read", "failed", "not_sent")
CATEGORIES = ("order_status", "price_stock", "complaint", "supplier", "other")


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
        "whatsapp_accounts",
        *_scope_columns(),
        sa.Column("phone_number_id", sa.String(40), nullable=False),
        sa.Column("business_account_id", sa.String(40), nullable=False),
        sa.Column("display_phone_number", sa.String(40), nullable=True),
        sa.Column("verified_name", sa.String(200), nullable=True),
        sa.Column("quality_rating", sa.String(20), nullable=True),
        sa.Column("access_token_ciphertext", sa.Text(), nullable=False),
        sa.Column("access_token_hint", sa.String(16), nullable=True),
        sa.Column("app_secret_ciphertext", sa.Text(), nullable=False),
        sa.Column("verify_token_hash", sa.String(64), nullable=False),
        sa.Column("owner_test_number", sa.String(20), nullable=True),
        sa.Column("auto_send_templates", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("triage_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(20), nullable=False, server_default="unverified"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_webhook_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", name="pk_whatsapp_accounts"),
        _workspace_fk("whatsapp_accounts"),
        sa.UniqueConstraint("phone_number_id", name="uq_whatsapp_accounts_phone_number_id"),
        sa.CheckConstraint("status in ('unverified', 'connected', 'error')", name="ck_whatsapp_accounts_status"),
        sa.CheckConstraint("verify_token_hash ~ '^[0-9a-f]{64}$'", name="ck_whatsapp_accounts_verify_token_hash"),
        sa.CheckConstraint("phone_number_id ~ '^[0-9]{5,20}$'", name="ck_whatsapp_accounts_phone_number_id"),
    )
    op.create_index("ix_whatsapp_accounts_verify_token_hash", "whatsapp_accounts", ["verify_token_hash"])

    op.create_table(
        "whatsapp_contacts",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope_columns(),
        sa.Column("wa_id", sa.String(32), nullable=False),
        sa.Column("profile_name", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _workspace_fk("whatsapp_contacts"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "wa_id", name="uq_whatsapp_contacts_wa_id"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "id", name="uq_whatsapp_contacts_scope_id"),
    )

    op.create_table(
        "whatsapp_conversations",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope_columns(),
        sa.Column("contact_id", sa.String(80), nullable=False),
        sa.Column("wa_id", sa.String(32), nullable=False),
        sa.Column("contact_name", sa.String(200), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_message_preview", sa.String(200), nullable=False, server_default=""),
        sa.Column("last_direction", sa.String(10), nullable=False),
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unread_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("category", sa.String(20), nullable=True),
        sa.Column("category_source", sa.String(10), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("suggestion", postgresql.JSONB(), nullable=True),
        sa.Column("needs_triage", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("triaged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _workspace_fk("whatsapp_conversations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "contact_id"],
            ["whatsapp_contacts.tenant_id", "whatsapp_contacts.workspace_id", "whatsapp_contacts.id"],
            name="fk_whatsapp_conversations_contact",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "workspace_id", "wa_id", name="uq_whatsapp_conversations_wa_id"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "id", name="uq_whatsapp_conversations_scope_id"),
        sa.CheckConstraint("last_direction in ('inbound', 'outbound')", name="ck_whatsapp_conversations_direction"),
        sa.CheckConstraint(
            "category is null or category in (" + ", ".join(f"'{value}'" for value in CATEGORIES) + ")",
            name="ck_whatsapp_conversations_category",
        ),
        sa.CheckConstraint("unread_count >= 0", name="ck_whatsapp_conversations_unread"),
    )
    # The inbox (newest first, keyset paging).
    op.create_index(
        "ix_whatsapp_conversations_scope_last",
        "whatsapp_conversations",
        ["tenant_id", "workspace_id", "last_message_at", "id"],
    )

    op.create_table(
        "whatsapp_messages",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope_columns(),
        sa.Column("conversation_id", sa.String(80), nullable=False),
        sa.Column("wamid", sa.String(200), nullable=True),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("media", postgresql.JSONB(), nullable=True),
        sa.Column("template", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("reply_to", sa.String(200), nullable=True),
        sa.Column("sent_by", sa.String(160), nullable=True),
        sa.Column("approval_id", sa.String(80), nullable=True),
        sa.Column("task_id", sa.String(80), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _workspace_fk("whatsapp_messages"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "conversation_id"],
            ["whatsapp_conversations.tenant_id", "whatsapp_conversations.workspace_id", "whatsapp_conversations.id"],
            name="fk_whatsapp_messages_conversation",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "workspace_id", "wamid", name="uq_whatsapp_messages_wamid"),
        sa.CheckConstraint("direction in ('inbound', 'outbound')", name="ck_whatsapp_messages_direction"),
        sa.CheckConstraint(
            "status in (" + ", ".join(f"'{value}'" for value in MESSAGE_STATUSES) + ")",
            name="ck_whatsapp_messages_status",
        ),
    )
    # A thread (newest first, keyset paging) and the "awaiting approval" filter.
    op.create_index(
        "ix_whatsapp_messages_thread",
        "whatsapp_messages",
        ["tenant_id", "workspace_id", "conversation_id", "timestamp", "id"],
    )
    op.create_index(
        "ix_whatsapp_messages_awaiting",
        "whatsapp_messages",
        ["tenant_id", "workspace_id", "conversation_id"],
        postgresql_where=sa.text("status = 'awaiting_approval'"),
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

    # Webhook routing as anum_maintenance (created by migration 0011 or a DBA): the
    # phone number id, verify token hash and scope of every account, nothing else.
    op.execute(
        "grant select (tenant_id, workspace_id, phone_number_id, verify_token_hash) "
        "on whatsapp_accounts to anum_maintenance"
    )
    op.execute(
        """
        create policy whatsapp_webhook_routing on whatsapp_accounts
        for select to anum_maintenance
        using (true)
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists whatsapp_webhook_routing on whatsapp_accounts")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on whatsapp_accounts from anum_maintenance;
          end if;
        end
        $$
        """
    )
    for table in reversed(TABLES):
        op.drop_table(table)
