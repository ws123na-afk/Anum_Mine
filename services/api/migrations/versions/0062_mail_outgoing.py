"""Email Center: scheduled send and outgoing attachments (docs/email.md#scheduled-send).

* ``mail_drafts`` gains ``send_at`` (when to send, UTC), ``approved_hash`` (the hash of the
  approved send call, checked again when the scheduler sends), ``send_lease_until`` (a claimed
  send; past it the send counts as interrupted and becomes ``unknown``, never retried) and
  ``attachment_note``. The status check admits ``scheduled`` (approved, waiting for its time).
* ``mail_attachments``: files attached to a message written in ANUM (name, type, size, SHA-256,
  object-storage key, whether a malware scanner saw it). Forced RLS like ``mail_drafts``: the
  tenant and workspace policy plus the restrictive owner policy.

The scheduler finds due work as ``anum_maintenance``: a column grant on the ids, scope, status
and the two times only (never subjects, bodies or addresses), and a policy that shows only rows
needing work. For that the owner policy of ``mail_drafts`` also admits
``current_user = 'anum_maintenance'``, as ``mail_accounts`` already does (a login that merely
inherits the role never is that user). Each due message is then claimed and sent as the
application role in its owner's scope.

Expand only: new nullable columns, a new table, a wider status check and policies. The previous
release ignores the new columns and table; it cannot read a ``scheduled`` row, so before rolling
back past this release cancel scheduled messages (docs/email.md#scheduled-send).
"""

import sqlalchemy as sa
from alembic import op

revision = "0062_mail_outgoing"
down_revision = "0061_mail_sharing_contacts"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"
DRAFT_OWNER_PREDICATE = f"{OWNER_PREDICATE}\n          or current_user = 'anum_maintenance'"
OLD_STATUSES = "'draft', 'pending_approval', 'sending', 'sent', 'failed', 'unknown'"
NEW_STATUSES = f"{OLD_STATUSES}, 'scheduled'"


def upgrade() -> None:
    op.add_column("mail_drafts", sa.Column("send_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("mail_drafts", sa.Column("approved_hash", sa.String(64), nullable=True))
    op.add_column("mail_drafts", sa.Column("send_lease_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("mail_drafts", sa.Column("attachment_note", sa.Text(), nullable=True))
    op.drop_constraint("ck_mail_drafts_status", "mail_drafts", type_="check")
    op.create_check_constraint("ck_mail_drafts_status", "mail_drafts", f"status in ({NEW_STATUSES})")
    op.create_index(
        "ix_mail_drafts_due", "mail_drafts", ["send_at"],
        postgresql_where=sa.text("status in ('scheduled', 'pending_approval') and send_at is not null"),
    )
    op.create_index(
        "ix_mail_drafts_lease", "mail_drafts", ["send_lease_until"],
        postgresql_where=sa.text("status = 'sending' and send_lease_until is not null"),
    )

    op.create_table(
        "mail_attachments",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
        sa.Column(
            "draft_id", sa.String(80),
            sa.ForeignKey("mail_drafts.id", ondelete="CASCADE", name="fk_mail_attachments_draft"), nullable=False,
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(160), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(600), nullable=False),
        sa.Column("scanned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_mail_attachments_workspace"
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_mail_attachments_size"),
    )
    op.create_index("ix_mail_attachments_draft", "mail_attachments", ["draft_id"])
    op.execute("alter table mail_attachments enable row level security")
    op.execute("alter table mail_attachments force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_mail_attachments on mail_attachments
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
        create policy owner_only_mail_attachments on mail_attachments
        as restrictive
        using (
          {OWNER_PREDICATE}
        )
        with check (
          {OWNER_PREDICATE}
        )
        """
    )

    # Scheduler discovery: ids, scope, status and the two times of rows that need work, nothing else.
    op.execute(
        f"""
        alter policy owner_only_mail_drafts on mail_drafts
        using (
          {DRAFT_OWNER_PREDICATE}
        )
        with check (
          {OWNER_PREDICATE}
        )
        """
    )
    op.execute(
        "grant select (id, tenant_id, workspace_id, owner_user_id, status, send_at, send_lease_until) "
        "on mail_drafts to anum_maintenance"
    )
    op.execute(
        """
        create policy maintenance_due_mail_drafts on mail_drafts
        for select to anum_maintenance
        using (
          (status in ('scheduled', 'pending_approval') and send_at is not null)
          or (status = 'sending' and send_lease_until is not null)
        )
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_due_mail_drafts on mail_drafts")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on mail_drafts from anum_maintenance;
          end if;
        end
        $$
        """
    )
    op.execute(
        f"""
        alter policy owner_only_mail_drafts on mail_drafts
        using (
          {OWNER_PREDICATE}
        )
        with check (
          {OWNER_PREDICATE}
        )
        """
    )
    op.drop_table("mail_attachments")
    op.drop_index("ix_mail_drafts_lease", table_name="mail_drafts")
    op.drop_index("ix_mail_drafts_due", table_name="mail_drafts")
    op.execute("update mail_drafts set status = 'draft' where status = 'scheduled'")
    op.drop_constraint("ck_mail_drafts_status", "mail_drafts", type_="check")
    op.create_check_constraint("ck_mail_drafts_status", "mail_drafts", f"status in ({OLD_STATUSES})")
    for column in ("attachment_note", "send_lease_until", "approved_hash", "send_at"):
        op.drop_column("mail_drafts", column)
