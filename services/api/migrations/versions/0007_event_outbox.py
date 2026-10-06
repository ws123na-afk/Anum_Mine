"""Durable event outbox on domain_events, relayed by a narrowly privileged role.

Every event row carries its own publication state, so an event and its "not yet
published" marker are written by the same insert in the request's transaction.
The relay (``anum_api.outbox_relay``) runs as ``anum_outbox_relay``: it may read
only unpublished events (across tenants, which it needs to publish them) and may
update only the four publication columns. It has no access to any other table.
The application role keeps its tenant-isolation policy unchanged.
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_event_outbox"
down_revision = "0006_workspace_invitations"
branch_labels = None
depends_on = None

RELAY_ROLE = "anum_outbox_relay"


def upgrade() -> None:
    op.add_column(
        "domain_events", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "domain_events",
        sa.Column("publish_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "domain_events",
        sa.Column(
            "publish_next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column("domain_events", sa.Column("publish_last_error", sa.Text(), nullable=True))

    # History recorded before the durable outbox existed was handled by the in-process
    # outbox (or never meant for a bus); do not republish it. RLS is forced on this
    # table even for its owner, so lift FORCE for the backfill only.
    op.execute("alter table domain_events no force row level security")
    op.execute("update domain_events set published_at = created_at where published_at is null")
    op.execute("alter table domain_events force row level security")

    op.create_index(
        "ix_domain_events_outbox_pending",
        "domain_events",
        ["publish_next_attempt_at", "created_at", "id"],
        postgresql_where=sa.text("published_at is null"),
    )

    # The role is NOLOGIN: deployments grant it to the relay's own login. Creating it
    # needs CREATEROLE; when a DBA has already created it, this is a no-op.
    op.execute(
        """
        do $$
        begin
          if not exists (select 1 from pg_roles where rolname = 'anum_outbox_relay') then
            create role anum_outbox_relay nologin;
          end if;
        end
        $$
        """
    )
    op.execute("grant usage on schema public to anum_outbox_relay")
    op.execute(
        """
        grant select (
          id, tenant_id, workspace_id, type, version, subject, correlation_id, payload,
          created_at, published_at, publish_attempts, publish_next_attempt_at
        ) on domain_events to anum_outbox_relay
        """
    )
    op.execute(
        """
        grant update (
          published_at, publish_attempts, publish_next_attempt_at, publish_last_error
        ) on domain_events to anum_outbox_relay
        """
    )
    # Permissive policies are OR-ed per role, so these widen access for the relay role
    # only. It sees unpublished events of every tenant and nothing once they are published.
    # PostgreSQL also checks the *new* row of an UPDATE against SELECT policies, so a row
    # stays visible while the transaction that marks it runs (now() is the transaction
    # start time); rows published by earlier transactions are invisible to the relay.
    op.execute(
        """
        create policy outbox_relay_read on domain_events
        for select to anum_outbox_relay
        using (published_at is null or published_at >= now())
        """
    )
    op.execute(
        """
        create policy outbox_relay_mark on domain_events
        for update to anum_outbox_relay
        using (published_at is null)
        with check (true)
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists outbox_relay_mark on domain_events")
    op.execute("drop policy if exists outbox_relay_read on domain_events")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_outbox_relay') then
            revoke all on domain_events from anum_outbox_relay;
            revoke usage on schema public from anum_outbox_relay;
          end if;
        end
        $$
        """
    )
    # The role itself is cluster-wide and may be granted to logins or used by other
    # databases, so it is left in place.
    op.drop_index("ix_domain_events_outbox_pending", table_name="domain_events")
    op.drop_column("domain_events", "publish_last_error")
    op.drop_column("domain_events", "publish_next_attempt_at")
    op.drop_column("domain_events", "publish_attempts")
    op.drop_column("domain_events", "published_at")
