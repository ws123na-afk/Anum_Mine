"""Email Center: every writer of a mailbox, and the inbox UIDVALIDITY of forwarding rules
(docs/email.md#forwarding-rules, docs/email.md#background-sync).

A mailbox shared to send has messages written by several people, each private to its writer
(the restrictive owner policy of ``mail_drafts``). Two jobs need to see past that, by ids only,
as ``anum_maintenance`` (migration 0011's pattern, never BYPASSRLS):

* **The hourly send limit of forwarding rules** counts every writer's recent sends of the
  mailbox, not only its owner's: ``maintenance_recent_sends_mail_drafts`` shows sent, sending
  and unknown messages changed in the last two hours, and ``updated_at`` is granted. Nothing
  else (no subject, body or address) is readable.
* **Disconnecting a mailbox** removes every writer's stored attachment files, not only the
  owner's. The owner first marks the mailbox ``disconnecting_at``; while it is marked,
  ``maintenance_disconnect_mail_drafts`` shows which people wrote in it, and each writer's files
  are then collected in that writer's own scope before the mailbox (and, by its foreign keys,
  every draft and file row) is deleted.

``mail_forward_rules.last_uidvalidity`` is the inbox UIDVALIDITY the rule's ``last_uid`` belongs
to; when the server reports another one the rule starts again from the newest message.

Expand only: two nullable columns, a column grant and two policies for the maintenance role.
"""

import sqlalchemy as sa
from alembic import op

revision = "0069_mail_writers_uidvalidity"
down_revision = "0068_mail_rules_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mail_accounts", sa.Column("disconnecting_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("mail_forward_rules", sa.Column("last_uidvalidity", sa.BigInteger(), nullable=True))
    op.execute("grant select (disconnecting_at) on mail_accounts to anum_maintenance")
    op.execute("grant select (updated_at) on mail_drafts to anum_maintenance")
    op.execute(
        """
        create policy maintenance_recent_sends_mail_drafts on mail_drafts
        for select to anum_maintenance
        using (
          status in ('sent', 'sending', 'unknown') and updated_at > now() - interval '2 hours'
        )
        """
    )
    op.execute(
        """
        create policy maintenance_disconnect_mail_drafts on mail_drafts
        for select to anum_maintenance
        using (
          exists (select 1 from mail_accounts a where a.id = mail_drafts.account_id and a.disconnecting_at is not null)
        )
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_disconnect_mail_drafts on mail_drafts")
    op.execute("drop policy if exists maintenance_recent_sends_mail_drafts on mail_drafts")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke select (updated_at) on mail_drafts from anum_maintenance;
            revoke select (disconnecting_at) on mail_accounts from anum_maintenance;
          end if;
        end
        $$
        """
    )
    op.drop_column("mail_forward_rules", "last_uidvalidity")
    op.drop_column("mail_accounts", "disconnecting_at")
