"""Tyre planning (docs/tyre-planning.md).

Four workspace tables, each under forced tenant and workspace RLS:

* ``tyre_connections``: one row per workspace: where the owner's tyre data is (Supabase
  PostgreSQL or REST, or Firestore), the read-only credential encrypted with
  ``ANUM_SECRETS_KEY`` (``secret_ciphertext``; only a hint is ever returned), the mapping
  onto ANUM's tyre model, the planning assumptions and the last discovered schema.
* ``tyre_snapshots``: the last synced dataset (after the mapping) and its sync statistics.
* ``tyre_alerts``: alerts raised from the dataset; one active alert per key (partial
  unique index), resolved ones kept 90 days.
* ``tyre_reports``: generated reports (content frozen at generation) and their delivery
  requests; a scheduled run produces at most one report (partial unique index on the
  idempotency key).

``notification_preferences.tyre_alerts`` (default true) is each user's switch for tyre
alert notifications.

``anum_maintenance`` may discover which workspaces hold an encrypted tyre credential
(``tenant_id`` and ``workspace_id`` of rows with a ciphertext, nothing else) so
``python -m anum_api.rotate_secrets`` can re-encrypt them inside each workspace's RLS
context, like model provider keys (migration 0009).

Expand only: new tables, a new nullable-safe column with a default and a new policy;
the previous release ignores all of them.

Chained after ``0036_learning``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0037_tyre_planning"
down_revision = "0036_learning"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = ("tyre_connections", "tyre_snapshots", "tyre_alerts", "tyre_reports")


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"
    )


def _scope_columns() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "tyre_connections",
        *_scope_columns(),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("endpoint", sa.String(500), nullable=False, server_default=""),
        sa.Column("schema_name", sa.String(63), nullable=False, server_default="public"),
        sa.Column("secret_ciphertext", sa.Text(), nullable=True),
        sa.Column("secret_hint", sa.String(200), nullable=True),
        sa.Column("mapping", postgresql.JSONB(), nullable=True),
        sa.Column("assumptions", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("discovery", postgresql.JSONB(), nullable=True),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="untested"),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", name="pk_tyre_connections"),
        _workspace_fk("tyre_connections"),
        sa.CheckConstraint(
            "kind in ('supabase_postgres', 'supabase_rest', 'firestore')", name="ck_tyre_connections_kind"
        ),
        sa.CheckConstraint("status in ('untested', 'ok', 'failed')", name="ck_tyre_connections_status"),
    )
    op.create_table(
        "tyre_snapshots",
        *_scope_columns(),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_by", sa.String(160), nullable=False),
        sa.Column("stats", postgresql.JSONB(), nullable=False),
        sa.Column("dataset", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", name="pk_tyre_snapshots"),
        _workspace_fk("tyre_snapshots"),
    )
    op.create_table(
        "tyre_alerts",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope_columns(),
        sa.Column("key", sa.String(300), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("subject_type", sa.String(20), nullable=False),
        sa.Column("subject_id", sa.String(200), nullable=False),
        sa.Column("subject_label", sa.String(300), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(160), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        _workspace_fk("tyre_alerts"),
        sa.CheckConstraint("severity in ('critical', 'warning', 'info')", name="ck_tyre_alerts_severity"),
        sa.CheckConstraint("status in ('open', 'acknowledged', 'resolved')", name="ck_tyre_alerts_status"),
    )
    op.create_index(
        "uq_tyre_alerts_active_key",
        "tyre_alerts",
        ["tenant_id", "workspace_id", "key"],
        unique=True,
        postgresql_where=sa.text("status <> 'resolved'"),
    )
    op.create_index("ix_tyre_alerts_scope_status", "tyre_alerts", ["tenant_id", "workspace_id", "status", "severity"])
    op.create_table(
        "tyre_reports",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope_columns(),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("period_from", sa.Date(), nullable=True),
        sa.Column("period_to", sa.Date(), nullable=True),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("schedule_id", sa.String(80), nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=True),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("deliveries", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        _workspace_fk("tyre_reports"),
        sa.CheckConstraint("source in ('manual', 'schedule')", name="ck_tyre_reports_source"),
    )
    op.create_index(
        "uq_tyre_reports_idempotency",
        "tyre_reports",
        ["tenant_id", "workspace_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key is not null"),
    )
    op.create_index("ix_tyre_reports_scope_created", "tyre_reports", ["tenant_id", "workspace_id", "created_at", "id"])

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

    op.add_column(
        "notification_preferences",
        sa.Column("tyre_alerts", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    # Key rotation discovery only (see the module docstring).
    op.execute("grant select (tenant_id, workspace_id) on tyre_connections to anum_maintenance")
    op.execute(
        """
        create policy maintenance_encrypted_tyre_credentials on tyre_connections
        for select to anum_maintenance
        using (secret_ciphertext is not null)
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_encrypted_tyre_credentials on tyre_connections")
    op.drop_column("notification_preferences", "tyre_alerts")
    for table in reversed(TABLES):
        op.drop_table(table)
