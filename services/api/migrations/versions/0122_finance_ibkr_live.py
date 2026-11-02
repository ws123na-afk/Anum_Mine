"""IBKR trading: connections, orders, settings and events; live IBKR orders may run (expand only).

docs/finance-live-trading.md. Owner decision of 2 November 2026: Interactive Brokers, US-listed
ETFs only, every order approved by the owner, small hard limits.

* ``finance_broker_settings``: one row per person and workspace (limits, allowed ETFs with their
  IBKR contract ids, the live switch, the kill switch, reconciliation), as JSON with a version.
* ``finance_broker_connections``: one active IBKR connection per person, workspace and
  environment (``paper`` or ``live``). ``secret`` is Fernet ciphertext of the gateway address,
  certificate fingerprint, account id and optional Flex token; it is erased (empty) when the
  connection is revoked. There is no password column: IBKR's gateway needs none from ANUM.
* ``finance_broker_orders``: every order ANUM sent or is sending; ``id`` is the execution
  intent's id and IBKR's client order id, so one intent is at most one order.
* ``finance_broker_events``: the audit trail and in-app alerts (no secrets, no account id).

Every table is under forced RLS with ``tenant_isolation_<table>``, a restrictive
``owner_only_<table>`` policy on ``owner_user_id = anum.user_id`` (like 0058) and the tenant
purge policy (``lifecycle_purge_<table>``, like 0097).

``ck_finance_exec_intents_no_live`` (0058) is replaced by ``ck_finance_exec_intents_live_ibkr``,
the narrowest change that lets a live order run: a live intent may be approved, executing,
executed or failed **only** when it is a person's own buy or sell **limit** order for a US
dollar **security** on an IBKR connection (``account_id`` starting ``ibkr:``) of at most
USD 1,000, approved by its owner. Every other live intent is still refused by the database
exactly as before. The server adds every other gate (deployment flag, the owner's switch with a
typed code, cooling-off, paper proof, limits, reconciliation, kill switches).

Expand only: new tables and one widened check. The previous release never writes a live intent
in those states and ignores the new tables, so it keeps working against this schema.

Chained after ``0121_meeting_agent_leave``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0122_finance_ibkr_live"
down_revision = "0121_meeting_agent_leave"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"
TABLES = ("finance_broker_settings", "finance_broker_connections", "finance_broker_orders", "finance_broker_events")
TS = sa.DateTime(timezone=True)
OLD_NO_LIVE = "mode <> 'live' or (status in ('pending_approval', 'denied', 'refused', 'expired', 'rejected') and approved_by is null)"
LIVE_IBKR = (
    "mode <> 'live' "
    "or (status in ('pending_approval', 'denied', 'refused', 'expired', 'rejected') and approved_by is null) "
    "or (kind = 'trade' and requested_by = 'person' and order_type = 'limit' and instrument_kind = 'security' "
    "and currency = 'USD' and side in ('buy', 'sell') and account_id like 'ibkr:%' "
    "and amount is not null and amount > 0 and amount <= 1000 and approved_by = owner_user_id)"
)


def _owned() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
    ]


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace")


def _rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(f"create policy tenant_isolation_{table} on {table} using ({PREDICATE}) with check ({PREDICATE})")
    op.execute(f"create policy owner_only_{table} on {table} as restrictive using ({OWNER_PREDICATE}) with check ({OWNER_PREDICATE})")
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to anum_tenant_purger")
    purge_policy = (f"create policy lifecycle_purge_{table} on {table} for all to anum_tenant_purger "  # nosec B608: fixed table name
        "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
        "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = "
        f"{table}.tenant_id and j.status = 'purging' "
        "and (j.kind = 'organisation_deletion' or "
        f"(j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id)))) "
        "with check (false)")
    op.execute(purge_policy)


def upgrade() -> None:
    op.create_table(
        "finance_broker_settings",
        *_owned(),
        sa.Column("data", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "owner_user_id", name="pk_finance_broker_settings"),
        _workspace_fk("finance_broker_settings"),
        sa.CheckConstraint("version >= 0", name="ck_finance_broker_settings_version"),
    )

    op.create_table(
        "finance_broker_connections",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("broker", sa.String(10), nullable=False, server_default="ibkr"),
        sa.Column("environment", sa.String(10), nullable=False),
        sa.Column("gateway_host", sa.String(80), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("hint", sa.String(40), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("session", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("session_message", sa.String(300), nullable=True),
        sa.Column("session_checked_at", TS, nullable=True),
        sa.Column("verified_at", TS, nullable=True),
        sa.Column("trading_type", sa.String(20), nullable=True),
        sa.Column("last_sync_at", TS, nullable=True),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("statement", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("finance_broker_connections"),
        sa.CheckConstraint("broker = 'ibkr'", name="ck_finance_broker_connections_broker"),
        sa.CheckConstraint("environment in ('paper', 'live')", name="ck_finance_broker_connections_env"),
        sa.CheckConstraint("gateway_host in ('ibkr-gateway', '127.0.0.1', 'localhost')", name="ck_finance_broker_connections_host"),
        sa.CheckConstraint("status in ('active', 'revoked')", name="ck_finance_broker_connections_status"),
        # Revoking erases the encrypted details; an active connection always has them.
        sa.CheckConstraint("(status = 'revoked') = (secret = '')", name="ck_finance_broker_connections_secret"),
        sa.CheckConstraint("session in ('unknown', 'authenticated', 'expired', 'unreachable')", name="ck_finance_broker_connections_session"),
    )
    op.create_index("ix_finance_broker_connections_owner", "finance_broker_connections", ["tenant_id", "workspace_id", "owner_user_id"])
    op.create_index("uq_finance_broker_connections_active", "finance_broker_connections",
                    ["tenant_id", "workspace_id", "owner_user_id", "environment"], unique=True,
                    postgresql_where=sa.text("status = 'active'"))

    op.create_table(
        "finance_broker_orders",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("connection_id", sa.String(80), nullable=False),
        sa.Column("environment", sa.String(10), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("conid", sa.BigInteger(), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("quantity", sa.Numeric(28, 8), nullable=False),
        sa.Column("limit_price", sa.Numeric(24, 10), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("broker_order_id", sa.String(40), nullable=True),
        sa.Column("filled_quantity", sa.Numeric(28, 8), nullable=False, server_default="0"),
        sa.Column("average_price", sa.Numeric(24, 10), nullable=True),
        sa.Column("message", sa.String(300), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("submitted_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("finance_broker_orders"),
        sa.CheckConstraint("environment in ('paper', 'live')", name="ck_finance_broker_orders_env"),
        sa.CheckConstraint("side in ('buy', 'sell')", name="ck_finance_broker_orders_side"),
        sa.CheckConstraint(
            "state in ('submitting', 'submitted', 'partially_filled', 'filled', 'cancel_requested', 'cancelled', 'rejected', 'unknown')",
            name="ck_finance_broker_orders_state",
        ),
        sa.CheckConstraint("quantity > 0 and limit_price > 0 and conid > 0", name="ck_finance_broker_orders_positive"),
        sa.CheckConstraint("filled_quantity >= 0 and filled_quantity <= quantity", name="ck_finance_broker_orders_filled"),
        sa.CheckConstraint("attempts >= 0", name="ck_finance_broker_orders_attempts"),
    )
    op.create_index("ix_finance_broker_orders_owner", "finance_broker_orders",
                    ["tenant_id", "workspace_id", "owner_user_id", "environment", "created_at"])

    op.create_table(
        "finance_broker_events",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("environment", sa.String(10), nullable=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("message", sa.String(400), nullable=False),
        sa.Column("ref", sa.String(80), nullable=True),
        sa.Column("alert", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("read_at", TS, nullable=True),
        _workspace_fk("finance_broker_events"),
        sa.CheckConstraint("environment is null or environment in ('paper', 'live')", name="ck_finance_broker_events_env"),
        sa.CheckConstraint("severity in ('info', 'warning', 'critical')", name="ck_finance_broker_events_severity"),
        sa.CheckConstraint("read_at is null or alert", name="ck_finance_broker_events_read"),
    )
    op.create_index("ix_finance_broker_events_owner", "finance_broker_events", ["tenant_id", "workspace_id", "owner_user_id", "created_at"])

    for table in TABLES:
        _rls(table)

    op.create_check_constraint("ck_finance_exec_intents_live_ibkr", "finance_exec_intents", LIVE_IBKR)
    op.drop_constraint("ck_finance_exec_intents_no_live", "finance_exec_intents", type_="check")


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup. Restoring the
    # old check fails while any live intent ran, which is the point.
    op.create_check_constraint("ck_finance_exec_intents_no_live", "finance_exec_intents", OLD_NO_LIVE)
    op.drop_constraint("ck_finance_exec_intents_live_ibkr", "finance_exec_intents", type_="check")
    for table in reversed(TABLES):
        op.execute(f"drop policy if exists lifecycle_purge_{table} on {table}")
        op.drop_table(table)
