"""Email Center: the workspace's own mail server over IMAP and SMTP (docs/email.md#email-center).

* ``mail_accounts``: one mailbox per row, private to the person who connected it, with its
  password only as Fernet ciphertext (``ANUM_SECRETS_KEY``) and its health.
* ``mail_drafts``: messages written in ANUM (new, reply, reply to all, forward) and their
  send state, with a Message-ID made before any attempt and an idempotency key unique per
  mailbox, so the same send is never submitted twice.

Mail itself stays on the mail server. Both tables have forced RLS: the standard tenant and
workspace policy plus a **restrictive** ``owner_user_id = anum.user_id`` policy, so a row is
visible and writable only to its owner. ``anum_maintenance`` may find mailboxes that hold a
password for key rotation (``rotate_secrets``): ids and scope only, through a column grant;
for that the owner policy of ``mail_accounts`` also admits ``current_user = 'anum_maintenance'``
(a login that merely inherits the role never is).

Expand only: two new tables; the previous release ignores them.

Chained after ``0059_finance_wider_live``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0060_email_center"
down_revision = "0059_finance_wider_live"
branch_labels = None
depends_on = None

TABLES = ("mail_accounts", "mail_drafts")
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"
ACCOUNT_OWNER_PREDICATE = f"{OWNER_PREDICATE}\n          or current_user = 'anum_maintenance'"


def _scope_columns() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
    ]


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"
    )


def upgrade() -> None:
    op.create_table(
        "mail_accounts",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope_columns(),
        sa.Column("address", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("password_enc", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="connected"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _workspace_fk("mail_accounts"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "address", name="uq_mail_accounts_address"),
        sa.CheckConstraint("status in ('connected', 'auth_failed', 'offline', 'error')", name="ck_mail_accounts_status"),
    )
    op.create_index("ix_mail_accounts_owner", "mail_accounts", ["tenant_id", "workspace_id", "owner_user_id"])
    op.create_table(
        "mail_drafts",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope_columns(),
        sa.Column(
            "account_id", sa.String(80),
            sa.ForeignKey("mail_accounts.id", ondelete="CASCADE", name="fk_mail_drafts_account"), nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("to_addresses", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("cc_addresses", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("bcc_addresses", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("subject", sa.Text(), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("in_reply_to", sa.String(500), nullable=True),
        sa.Column("references_header", sa.Text(), nullable=True),
        sa.Column("source_folder", sa.String(500), nullable=True),
        sa.Column("source_uid", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.String(500), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("approval_id", sa.String(80), nullable=True),
        sa.Column("task_id", sa.String(80), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("saved_to_sent", sa.Boolean(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _workspace_fk("mail_drafts"),
        sa.UniqueConstraint("account_id", "idempotency_key", name="uq_mail_drafts_idempotency"),
        sa.UniqueConstraint("account_id", "message_id", name="uq_mail_drafts_message_id"),
        sa.CheckConstraint("kind in ('new', 'reply', 'reply_all', 'forward')", name="ck_mail_drafts_kind"),
        sa.CheckConstraint(
            "status in ('draft', 'pending_approval', 'sending', 'sent', 'failed', 'unknown')", name="ck_mail_drafts_status"
        ),
    )
    op.create_index("ix_mail_drafts_owner_updated", "mail_drafts", ["tenant_id", "workspace_id", "owner_user_id", "updated_at"])
    for table in TABLES:
        owner = ACCOUNT_OWNER_PREDICATE if table == "mail_accounts" else OWNER_PREDICATE
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
        op.execute(
            f"""
            create policy owner_only_{table} on {table}
            as restrictive
            using (
              {owner}
            )
            with check (
              {OWNER_PREDICATE}
            )
            """
        )
    # Key rotation (python -m anum_api.rotate_secrets): mailboxes, ids and scope only.
    op.execute("grant select (id, tenant_id, workspace_id, owner_user_id) on mail_accounts to anum_maintenance")
    op.execute(
        """
        create policy maintenance_mail_rotation on mail_accounts
        for select to anum_maintenance
        using (true)
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_mail_rotation on mail_accounts")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on mail_accounts from anum_maintenance;
          end if;
        end
        $$
        """
    )
    for table in reversed(TABLES):
        op.drop_table(table)
