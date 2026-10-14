"""Finance stages 3 to 5: safe-to-spend, the Guardian rules, read-only connectors (docs/finance.md).

* ``finance_goals.monthly_contribution`` (nullable): what the person sets aside each month.
* ``finance_plans``: the safe-to-spend buffer per currency and whether to show a converted
  total (one row per person and workspace).
* ``finance_guardian_versions``: every confirmed version of the person's rules; a change
  adds a version (unique per person), nothing is overwritten.
* ``finance_guardian_changes``: proposed rules waiting for the person's confirmation with
  the payload hash they were shown.
* ``finance_connections``: read-only connections. A check constraint refuses any scope
  that does not end in ``:read``; credentials are Fernet ciphertext (``ANUM_SECRETS_KEY``).
* ``finance_fx_rates``: recorded exchange rates with their date and source, unique per
  person, pair, date and source; deleted with the connection that fetched them.

Every table belongs to one tenant and workspace (foreign key to ``workspaces``) and is under
forced RLS with ``tenant_isolation_<table>`` and a restrictive ``owner_only_<table>``
policy on ``owner_user_id = anum.user_id``, like migration 0056.

``anum_maintenance`` may discover due connections for the background sync: the ``id``,
``tenant_id``, ``workspace_id``, ``owner_user_id``, ``enabled``, ``status`` and
``next_sync_at`` columns of enabled, connected rows whose ``next_sync_at`` has passed,
nothing else (no credentials, no data). The restrictive owner policy on
``finance_connections`` therefore also admits the session whose current role is
``anum_maintenance`` (``current_user``, which a login that merely inherits the role never
has); that role can still read only those columns of due rows, or of rows holding
credentials (for ``python -m anum_api.rotate_secrets``), and cannot write. The sync and
the rotation run as the application role in the owner's own scope.

Expand only: one nullable column and new tables. The previous release ignores them.

Chained after ``0056_finance_ledger``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0057_finance_plan_guard_conn"
down_revision = "0056_finance_ledger"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"
CONNECTION_OWNER_PREDICATE = f"{OWNER_PREDICATE}\n          or current_user = 'anum_maintenance'"
TABLES = (
    "finance_plans",
    "finance_guardian_versions",
    "finance_guardian_changes",
    "finance_connections",
    "finance_fx_rates",
)
TS = sa.DateTime(timezone=True)
MONEY = sa.Numeric(20, 4)
CURRENCY = "~ '^[A-Z]{3}$'"


def _owned() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
    ]


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"
    )


def _owner_index(table: str, *columns: str) -> None:
    op.create_index(f"ix_{table}_owner", table, ["tenant_id", "workspace_id", "owner_user_id", *columns])


def upgrade() -> None:
    op.add_column("finance_goals", sa.Column("monthly_contribution", MONEY, nullable=True))

    op.create_table(
        "finance_plans",
        *_owned(),
        sa.Column("buffers", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("combine_currencies", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("fx_max_age_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "owner_user_id", name="pk_finance_plans"),
        _workspace_fk("finance_plans"),
        sa.CheckConstraint("fx_max_age_days between 1 and 60", name="ck_finance_plans_fx_age"),
    )

    op.create_table(
        "finance_guardian_versions",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("rules", postgresql.JSONB(), nullable=False),
        sa.Column("change_id", sa.String(80), nullable=False),
        sa.Column("confirmed_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("finance_guardian_versions"),
        sa.CheckConstraint("version >= 1", name="ck_finance_guardian_versions_version"),
        # Two confirmations of the same version can never both land.
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "version", name="uq_finance_guardian_versions_version"),
    )

    op.create_table(
        "finance_guardian_changes",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("rules", postgresql.JSONB(), nullable=False),
        sa.Column("proposed_by", sa.String(10), nullable=False),
        sa.Column("proposer", sa.String(160), nullable=False),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("loosens", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("decided_at", TS, nullable=True),
        _workspace_fk("finance_guardian_changes"),
        sa.CheckConstraint("proposed_by in ('person', 'agent')", name="ck_finance_guardian_changes_by"),
        sa.CheckConstraint(
            "status in ('pending', 'confirmed', 'rejected', 'expired', 'superseded')", name="ck_finance_guardian_changes_status"
        ),
        sa.CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="ck_finance_guardian_changes_hash"),
        # An assistant may only propose stricter rules: never one that loosens anything.
        sa.CheckConstraint(
            "proposed_by = 'person' or loosens = '[]'::jsonb", name="ck_finance_guardian_changes_agent_strict"
        ),
    )
    _owner_index("finance_guardian_changes", "status", "created_at")

    op.create_table(
        "finance_connections",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("provider", sa.String(60), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), nullable=False),
        sa.Column("credentials_encrypted", sa.Text(), nullable=True),
        sa.Column("credential_hint", sa.String(40), nullable=True),
        sa.Column("settings", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("next_sync_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("last_sync_at", TS, nullable=True),
        sa.Column("last_success_at", TS, nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("last_counts", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("finance_connections"),
        sa.CheckConstraint(
            "kind in ('bank', 'card', 'broker', 'crypto', 'market_data', 'fx')", name="ck_finance_connections_kind"
        ),
        sa.CheckConstraint("status in ('active', 'error', 'disconnected')", name="ck_finance_connections_status"),
        # Read only, enforced by the database: every scope ends in ":read", and there is one.
        sa.CheckConstraint(
            "jsonb_typeof(scopes) = 'array' and jsonb_array_length(scopes) > 0 "
            # "[:]" rather than a bare colon, which SQLAlchemy would read as a bind parameter.
            "and not jsonb_path_exists(scopes, '$[*] ? (!(@ like_regex \"[:]read$\"))')",
            name="ck_finance_connections_read_only",
        ),
        # A disconnected connection keeps no credentials.
        sa.CheckConstraint(
            "status <> 'disconnected' or credentials_encrypted is null", name="ck_finance_connections_wiped"
        ),
    )
    _owner_index("finance_connections", "created_at")
    op.create_index("ix_finance_connections_due", "finance_connections", ["next_sync_at"], postgresql_where=sa.text("enabled"))

    op.create_table(
        "finance_fx_rates",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("base", sa.String(3), nullable=False),
        sa.Column("quote", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(24, 10), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("source_url", sa.String(300), nullable=True),
        sa.Column("connection_id", sa.String(80), nullable=True),
        sa.Column("fetched_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("finance_fx_rates"),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["finance_connections.id"], name="fk_finance_fx_rates_connection", ondelete="CASCADE"
        ),
        sa.CheckConstraint(f"base {CURRENCY} and quote {CURRENCY} and base <> quote", name="ck_finance_fx_rates_pair"),
        sa.CheckConstraint("rate > 0", name="ck_finance_fx_rates_positive"),
        sa.UniqueConstraint(
            "tenant_id", "workspace_id", "owner_user_id", "base", "quote", "as_of", "source", name="uq_finance_fx_rates_day"
        ),
    )
    _owner_index("finance_fx_rates", "as_of")

    for table in TABLES:
        owner = CONNECTION_OWNER_PREDICATE if table == "finance_connections" else OWNER_PREDICATE
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

    # Discovery for the background sync (anum_api/finance/sync.py): due connections only.
    op.execute(
        "grant select (id, tenant_id, workspace_id, owner_user_id, enabled, status, next_sync_at) "
        "on finance_connections to anum_maintenance"
    )
    op.execute(
        """
        create policy maintenance_due_finance_connections on finance_connections
        for select to anum_maintenance
        using (enabled and status <> 'disconnected' and next_sync_at <= now())
        """
    )
    # Key rotation (python -m anum_api.rotate_secrets): connections that hold credentials.
    op.execute(
        """
        create policy maintenance_finance_rotation on finance_connections
        for select to anum_maintenance
        using (credentials_encrypted is not null)
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_finance_rotation on finance_connections")
    op.execute("drop policy if exists maintenance_due_finance_connections on finance_connections")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on finance_connections from anum_maintenance;
          end if;
        end
        $$
        """
    )
    for table in reversed(TABLES):
        op.drop_table(table)
    op.drop_column("finance_goals", "monthly_contribution")
