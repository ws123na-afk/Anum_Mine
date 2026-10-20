"""Calendar sync with Google Calendar and Microsoft 365 (docs/calendar.md#sync).

* ``calendar_connections``: one connected Google or Microsoft calendar per row (the account's
  primary calendar), with its OAuth tokens as Fernet ciphertext (``ANUM_SECRETS_KEY``), the
  incremental sync cursor (Google ``nextSyncToken`` or Microsoft ``deltaLink``) and health.
* ``calendar_oauth_requests``: pending connections (PKCE verifier, the returned code),
  short-lived.
* ``calendar_events`` gains the link to the provider event: ``connection_id``, ``external_id``,
  ``external_etag``, ``synced_at`` (when ANUM and the provider last agreed), ``sync_state``
  (``synced``, ``pending_push``, ``local``) and ``invites_hash`` (the content hash of the last
  invitations an owner approved). A provider event maps to at most one ANUM event per
  connection (partial unique index).

Both new tables belong to a tenant and workspace under forced RLS. ``anum_maintenance`` may
discover connections for the background sync (``id``, ``tenant_id``, ``workspace_id``,
``connected_by``, ``status``, ``last_sync_at`` of connections that do not need reconnecting)
and for key rotation (rows holding a token); never a token, an address or a write. The sync
itself runs as the application role inside each connection's tenant and workspace context.
Organisation and workspace deletion may remove the rows (0079's purge policy). Grant
``anum_maintenance`` to the API login ``WITH INHERIT FALSE, SET TRUE``.

Expand only: two new tables and nullable columns; the previous release ignores them.

Numbered 0091 and chained after ``0082_business_pipeline``, the head of ``main`` when written;
renumber at merge if another migration lands first.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0091_calendar_sync"
down_revision = "0082_business_pipeline"
branch_labels = None
depends_on = None

TABLES = ("calendar_connections", "calendar_oauth_requests")
TS = sa.DateTime(timezone=True)
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TENANT = "nullif(current_setting('anum.tenant_id', true), '')"
PURGER = "anum_tenant_purger"
JOBS = "tenant_lifecycle_jobs"


def _scope_columns() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
    ]


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


def _purge_policy(table: str) -> None:
    """Organisation or workspace deletion may remove these rows (0079's ``_purge_policy``)."""
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to {PURGER}")
    op.execute(
        f"""
        create policy lifecycle_purge_{table} on {table}
        for all to {PURGER}
        using (
          tenant_id = {TENANT}
          and exists (
            select 1 from {JOBS} j
            where j.tenant_id = {table}.tenant_id and j.status = 'purging'
              and (j.kind = 'organisation_deletion'
                   or (j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id))
          )
        )
        with check (false)
        """  # nosec B608 - fixed table names
    )


def upgrade() -> None:
    op.create_table(
        "calendar_connections",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope_columns(),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("connected_by", sa.String(160), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="connected"),
        sa.Column("scopes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("access_token_enc", sa.Text(), nullable=True),
        sa.Column("refresh_token_enc", sa.Text(), nullable=True),
        sa.Column("access_expires_at", TS, nullable=True),
        sa.Column("sync_cursor", sa.Text(), nullable=True),
        sa.Column("last_sync_at", TS, nullable=True),
        sa.Column("last_sync_status", sa.String(20), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_summary", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("sync_lease_until", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("calendar_connections"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "provider", "email", name="uq_calendar_connections_account"),
        sa.CheckConstraint("provider in ('google', 'microsoft')", name="ck_calendar_connections_provider"),
        sa.CheckConstraint(
            "status in ('connected', 'error', 'reauth_required')", name="ck_calendar_connections_status"
        ),
    )
    op.create_table(
        "calendar_oauth_requests",
        sa.Column("nonce", sa.String(80), primary_key=True),
        *_scope_columns(),
        sa.Column("user_id", sa.String(160), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("code_verifier_enc", sa.Text(), nullable=True),
        sa.Column("code_enc", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", TS, nullable=False),
        _workspace_fk("calendar_oauth_requests"),
    )
    op.add_column(
        "calendar_events",
        sa.Column(
            "connection_id",
            sa.String(80),
            sa.ForeignKey("calendar_connections.id", ondelete="SET NULL", name="fk_calendar_events_connection"),
            nullable=True,
        ),
    )
    op.add_column("calendar_events", sa.Column("external_id", sa.String(1024), nullable=True))
    op.add_column("calendar_events", sa.Column("external_etag", sa.String(200), nullable=True))
    op.add_column("calendar_events", sa.Column("synced_at", TS, nullable=True))
    op.add_column("calendar_events", sa.Column("sync_state", sa.String(20), nullable=True))
    op.add_column("calendar_events", sa.Column("invites_hash", sa.String(64), nullable=True))
    op.create_check_constraint(
        "ck_calendar_events_sync_state",
        "calendar_events",
        "sync_state is null or sync_state in ('synced', 'pending_push', 'local')",
    )
    # One ANUM event per provider event of a connection.
    op.create_index(
        "uq_calendar_events_external",
        "calendar_events",
        ["connection_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id is not null"),
    )
    for table in TABLES:
        _enable_rls(table)
        _purge_policy(table)
    # Background sync discovery (anum_api.calendar_sync.CalendarSyncScheduler): ids and scope only.
    op.execute(
        "grant select (id, tenant_id, workspace_id, connected_by, status, last_sync_at) "
        "on calendar_connections to anum_maintenance"
    )
    op.execute(
        """
        create policy maintenance_calendar_sync on calendar_connections
        for select to anum_maintenance
        using (status <> 'reauth_required')
        """
    )
    # Key rotation discovery: connections that hold a token, whatever their sync status.
    op.execute(
        """
        create policy maintenance_calendar_rotation on calendar_connections
        for select to anum_maintenance
        using (access_token_enc is not null or refresh_token_enc is not null)
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_calendar_rotation on calendar_connections")
    op.execute("drop policy if exists maintenance_calendar_sync on calendar_connections")
    op.drop_index("uq_calendar_events_external", table_name="calendar_events")
    op.drop_constraint("ck_calendar_events_sync_state", "calendar_events", type_="check")
    for column in ("invites_hash", "sync_state", "synced_at", "external_etag", "external_id", "connection_id"):
        op.drop_column("calendar_events", column)
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on calendar_connections from anum_maintenance;
          end if;
        end
        $$
        """
    )
    for table in reversed(TABLES):
        op.drop_table(table)
