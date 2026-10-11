"""Retention for assistant conversations (docs/conversations.md#retention).

``anum_maintenance`` may discover idle conversations: the ``id``, ``tenant_id``,
``workspace_id``, ``user_id`` and ``updated_at`` columns of ``conversations`` rows with
no activity for at least a day, and nothing else (no title, no turn, no write). The
retention purge then deletes the conversations idle for longer than
``ANUM_CONVERSATION_RETENTION_DAYS`` (their turns cascade) as the application role inside
each user's tenant, workspace and user RLS context (``anum_api/conversation_retention.py``),
never with ``BYPASSRLS``. The one-day floor keeps active conversations out of the
role's sight whatever retention an operator configures (the setting is 0 or at least 1).

Grant ``anum_maintenance`` to the API login ``WITH INHERIT FALSE, SET TRUE`` (as
``infra/helm/bootstrap-database.sql`` does): the new policy admits rows of every tenant,
so an inheriting grant would widen the application role's own queries.

Expand only: the previous release never reads the new policy.
"""

from alembic import op

revision = "0019_conversation_retention"
down_revision = "0018_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "grant select (id, tenant_id, workspace_id, user_id, updated_at) on conversations to anum_maintenance"
    )
    # Permissive policies are OR-ed per role, so this widens access for this role only.
    op.execute(
        """
        create policy maintenance_idle_conversations on conversations
        for select to anum_maintenance
        using (updated_at <= now() - interval '1 day')
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_idle_conversations on conversations")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on conversations from anum_maintenance;
          end if;
        end
        $$
        """
    )
