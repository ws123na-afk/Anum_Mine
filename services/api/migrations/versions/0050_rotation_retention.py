"""Key rotation reaches mailbox tokens and commerce secrets; WhatsApp history follows a retention setting.

* ``email_accounts``: ``python -m anum_api.rotate_secrets`` must find every mailbox that
  still holds an OAuth token, including those waiting for the owner to reconnect
  (``reauth_required``), which the background-sync policy of ``0035_email_inbox`` hides.
  A second permissive policy lets ``anum_maintenance`` see the rows that hold a token
  ciphertext. Its column grant is unchanged (``id``, ``tenant_id``, ``workspace_id``,
  ``connected_by``, ``status``, ``last_sync_at``): it still cannot read a token or an
  address, and it cannot write. The email sync scheduler keeps filtering on ``status``
  itself, so it does not start syncing mailboxes that need reconnecting.
* ``commerce_connectors`` and ``commerce_customers`` (migration ``0038_commerce``): key
  rotation must find every connector holding encrypted credentials (the sync policy only
  admits enabled connectors that are due) and every customer row holding encrypted
  buyer details. ``anum_maintenance`` gets a policy for connectors with a ciphertext (its
  column grant of ``id``, ``tenant_id``, ``workspace_id``, ``enabled``, ``next_sync_at``
  is unchanged) and ``id``, ``tenant_id``, ``workspace_id`` of customers with a
  ciphertext; never a ciphertext, never a name or an address, no write.
* ``automation_triggers`` (migration ``0049_automation_triggers``): key rotation finds
  every webhook trigger holding an HMAC secret ciphertext, enabled or not, through the
  existing column grant (ids, scope, kind, enabled); never the ciphertext.
* ``whatsapp_accounts.message_retention_days``: the workspace's WhatsApp history
  retention (null: the deployment default ``ANUM_WHATSAPP_MESSAGE_RETENTION_DAYS``;
  0: keep until deleted; 1 to 3650 days).
* ``whatsapp_conversations``: the retention purge (``anum_api.whatsapp_retention``, run
  by the daily retention job) discovers workspaces whose conversations are at least a
  day old as ``anum_maintenance``: ``tenant_id``, ``workspace_id`` and ``created_at``
  only, nothing else. The purge itself runs as the application role inside each
  workspace's RLS context and reads the retention there.

Grant ``anum_maintenance`` to the API login ``WITH INHERIT FALSE, SET TRUE`` (as
``infra/helm/bootstrap-database.sql`` does): these policies admit rows of every tenant.

Expand only: one nullable column, two column grants and five policies; the previous
release ignores all of them.

Chained after ``0047_chat_cards`` (renumbered from a parallel branch, CLAUDE.md).
"""

import sqlalchemy as sa
from alembic import op

revision = "0050_rotation_retention"
down_revision = "0047_chat_cards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Key rotation discovery: mailboxes that hold a token, whatever their sync status.
    op.execute(
        """
        create policy maintenance_email_rotation on email_accounts
        for select to anum_maintenance
        using (access_token_enc is not null or refresh_token_enc is not null)
        """
    )

    op.add_column("whatsapp_accounts", sa.Column("message_retention_days", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_whatsapp_accounts_message_retention",
        "whatsapp_accounts",
        "message_retention_days is null or message_retention_days between 0 and 3650",
    )

    # Key rotation discovery for commerce: connectors and customers that hold a ciphertext.
    op.execute(
        """
        create policy maintenance_commerce_rotation on commerce_connectors
        for select to anum_maintenance
        using (credentials_ciphertext is not null)
        """
    )
    op.execute("grant select (id, tenant_id, workspace_id) on commerce_customers to anum_maintenance")
    op.execute(
        """
        create policy maintenance_commerce_customer_rotation on commerce_customers
        for select to anum_maintenance
        using (
          name_ciphertext is not null or email_ciphertext is not null
          or phone_ciphertext is not null or address_ciphertext is not null
        )
        """
    )

    # Key rotation discovery for automation webhook triggers (0049 grants id and scope
    # columns; its own policy admits only enabled event triggers).
    op.execute(
        """
        create policy maintenance_trigger_rotation on automation_triggers
        for select to anum_maintenance
        using (secret_ciphertext is not null)
        """
    )

    # WhatsApp retention discovery: scope and age of conversations at least a day old.
    op.execute("grant select (tenant_id, workspace_id, created_at) on whatsapp_conversations to anum_maintenance")
    op.execute(
        """
        create policy maintenance_whatsapp_retention on whatsapp_conversations
        for select to anum_maintenance
        using (created_at < now() - interval '1 day')
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_whatsapp_retention on whatsapp_conversations")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on whatsapp_conversations from anum_maintenance;
          end if;
        end
        $$
        """
    )
    op.drop_constraint("ck_whatsapp_accounts_message_retention", "whatsapp_accounts", type_="check")
    op.drop_column("whatsapp_accounts", "message_retention_days")
    op.execute("drop policy if exists maintenance_trigger_rotation on automation_triggers")
    op.execute("drop policy if exists maintenance_email_rotation on email_accounts")
    op.execute("drop policy if exists maintenance_commerce_customer_rotation on commerce_customers")
    op.execute("drop policy if exists maintenance_commerce_rotation on commerce_connectors")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on commerce_customers from anum_maintenance;
          end if;
        end
        $$
        """
    )
