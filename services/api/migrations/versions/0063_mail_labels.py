"""Email Center: labels from AI on email (docs/email.md#ai-on-email).

``mail_labels``: the category and priority AI (or ANUM's rules) gave a message, so the list can
show them. One row per person, mailbox, folder and UID, with the message's Message-ID so a
reused UID never shows another message's label. No subject, sender or body is stored; the
reason is one short sentence. Labels are private to the person who asked for them (shared
mailboxes give every person their own): forced RLS on tenant and workspace plus a restrictive
``owner_user_id = anum.user_id`` policy, as ``mail_drafts`` has. Deleting the mailbox deletes
its labels (foreign key, on delete cascade).

Expand only: a new table; the previous release ignores it.

Chained after ``0062_mail_outgoing``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0063_mail_labels"
down_revision = "0062_mail_outgoing"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"
CATEGORIES = "'customer', 'order', 'invoice', 'supplier', 'internal', 'newsletter', 'notification', 'personal', 'spam', 'other'"


def upgrade() -> None:
    op.create_table(
        "mail_labels",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
        sa.Column(
            "account_id", sa.String(80),
            sa.ForeignKey("mail_accounts.id", ondelete="CASCADE", name="fk_mail_labels_account"), nullable=False,
        ),
        sa.Column("folder", sa.String(500), nullable=False),
        sa.Column("uid", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.String(500), nullable=True),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("priority", sa.String(10), nullable=False),
        sa.Column("reason", sa.String(240), nullable=False, server_default=""),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("model", sa.String(160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_mail_labels_workspace"),
        sa.CheckConstraint(f"category in ({CATEGORIES})", name="ck_mail_labels_category"),
        sa.CheckConstraint("priority in ('high', 'normal', 'low')", name="ck_mail_labels_priority"),
        sa.CheckConstraint("source in ('model', 'rules')", name="ck_mail_labels_source"),
        sa.CheckConstraint("uid > 0", name="ck_mail_labels_uid"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "account_id", "folder", "uid", name="uq_mail_labels_message"),
    )
    op.create_index("ix_mail_labels_account", "mail_labels", ["account_id"])
    op.execute("alter table mail_labels enable row level security")
    op.execute("alter table mail_labels force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_mail_labels on mail_labels
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
        create policy owner_only_mail_labels on mail_labels
        as restrictive
        using (
          {OWNER_PREDICATE}
        )
        with check (
          {OWNER_PREDICATE}
        )
        """
    )


def downgrade() -> None:
    op.drop_table("mail_labels")
