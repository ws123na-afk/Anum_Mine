"""Finance stages 6 to 8: paper and shadow trading, the execution gateway, limited live (off).

* ``finance_prices``: prices the person recorded (source ``manual``), unique per person,
  symbol, currency, date and source. ANUM has no market-price source and invents none.
* ``finance_paper_portfolios``: paper (simulated cash) and shadow (no cash) portfolios with
  their fee and slippage assumptions and the current run (``epoch``).
* ``finance_paper_orders``: SIMULATED orders and what they did (waiting, filled, refused,
  cancelled), one per execution intent (unique ``intent_id``: the idempotency key), deleted
  with their portfolio.
* ``finance_exec_intents``: every request through the execution gateway with the Guardian
  decision, the payload hash the person approves, the expiry, and who approved it once.
  Database checks: only the owner can be the approver; an executed request was approved;
  an agent can only make paper or shadow requests; **a live request can never be approved,
  executing or executed** (no live executor exists; a later contract migration would be
  needed to change that, with the owner's written approval).
* ``finance_exec_settings``: the person's own kill switch and live limits and opt-in.
* ``finance_exec_global``: the organisation's kill switch, one row per tenant.

Every per-person table belongs to one tenant and workspace (foreign key to ``workspaces``)
and is under forced RLS with ``tenant_isolation_<table>`` and a restrictive
``owner_only_<table>`` policy on ``owner_user_id = anum.user_id``, like migrations 0056 and
0057. ``finance_exec_global`` belongs to a tenant (foreign key to ``tenants``) and is under
forced RLS with ``tenant_isolation_finance_exec_global`` on ``anum.tenant_id``: it holds no
personal finance, only whether the switch is on. No role is granted anything new.

Expand only: new tables. The previous release ignores them.

Chained after ``0057_finance_plan_guard_conn``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0058_finance_paper_exec"
down_revision = "0057_finance_plan_guard_conn"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TENANT_PREDICATE = "tenant_id = nullif(current_setting('anum.tenant_id', true), '')"
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"
TABLES = (
    "finance_prices",
    "finance_paper_portfolios",
    "finance_paper_orders",
    "finance_exec_intents",
    "finance_exec_settings",
)
TS = sa.DateTime(timezone=True)
MONEY = sa.Numeric(20, 4)
PRICE = sa.Numeric(24, 10)
QUANTITY = sa.Numeric(28, 8)
BPS = sa.Numeric(7, 2)
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
    op.create_table(
        "finance_prices",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("price", PRICE, nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("connection_id", sa.String(80), nullable=True),
        sa.Column("recorded_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("finance_prices"),
        sa.CheckConstraint(f"currency {CURRENCY}", name="ck_finance_prices_currency"),
        sa.CheckConstraint("price > 0", name="ck_finance_prices_positive"),
        sa.UniqueConstraint(
            "tenant_id", "workspace_id", "owner_user_id", "symbol", "currency", "as_of", "source", name="uq_finance_prices_day"
        ),
    )
    _owner_index("finance_prices", "symbol", "as_of")

    op.create_table(
        "finance_paper_portfolios",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("starting_cash", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("fee_bps", BPS, nullable=False),
        sa.Column("fee_min", MONEY, nullable=False),
        sa.Column("slippage_bps", BPS, nullable=False),
        sa.Column("max_price_age_days", sa.Integer(), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("reset_at", TS, nullable=True),
        _workspace_fk("finance_paper_portfolios"),
        sa.CheckConstraint("mode in ('paper', 'shadow')", name="ck_finance_paper_portfolios_mode"),
        sa.CheckConstraint(
            "fee_bps between 0 and 1000 and slippage_bps between 0 and 1000 and fee_min >= 0", name="ck_finance_paper_portfolios_costs"
        ),
        sa.CheckConstraint("max_price_age_days between 1 and 30 and epoch >= 1", name="ck_finance_paper_portfolios_bounds"),
    )
    _owner_index("finance_paper_portfolios", "created_at")

    op.create_table(
        "finance_paper_orders",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("portfolio_id", sa.String(80), nullable=False),
        sa.Column("intent_id", sa.String(80), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("instrument_kind", sa.String(10), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("order_type", sa.String(10), nullable=False),
        sa.Column("quantity", QUANTITY, nullable=False),
        sa.Column("limit_price", PRICE, nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("reason_code", sa.String(40), nullable=True),
        sa.Column("reason", sa.String(300), nullable=True),
        sa.Column("requested_by", sa.String(10), nullable=False),
        sa.Column("market_price", PRICE, nullable=True),
        sa.Column("fill_price", PRICE, nullable=True),
        sa.Column("price_as_of", sa.Date(), nullable=True),
        sa.Column("price_source", sa.String(240), nullable=True),
        sa.Column("notional", MONEY, nullable=True),
        sa.Column("fee", MONEY, nullable=True),
        sa.Column("slippage", MONEY, nullable=True),
        sa.Column("guardian_outcome", sa.String(20), nullable=True),
        sa.Column("guardian_codes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("filled_at", TS, nullable=True),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("finance_paper_orders"),
        sa.ForeignKeyConstraint(
            ["portfolio_id"], ["finance_paper_portfolios.id"], name="fk_finance_paper_orders_portfolio", ondelete="CASCADE"
        ),
        # One simulated order per execution intent, ever: the gateway's idempotency key.
        sa.UniqueConstraint("intent_id", name="uq_finance_paper_orders_intent"),
        sa.CheckConstraint("mode in ('paper', 'shadow')", name="ck_finance_paper_orders_mode"),
        sa.CheckConstraint("instrument_kind in ('security', 'fx')", name="ck_finance_paper_orders_instrument"),
        sa.CheckConstraint("side in ('buy', 'sell') and order_type in ('market', 'limit')", name="ck_finance_paper_orders_side"),
        sa.CheckConstraint("status in ('waiting', 'filled', 'refused', 'cancelled')", name="ck_finance_paper_orders_status"),
        sa.CheckConstraint(f"quantity > 0 and currency {CURRENCY}", name="ck_finance_paper_orders_quantity"),
        sa.CheckConstraint(
            "status <> 'filled' or (fill_price is not null and notional is not null and fee is not null and filled_at is not null)",
            name="ck_finance_paper_orders_filled",
        ),
    )
    _owner_index("finance_paper_orders", "portfolio_id", "epoch", "created_at")

    op.create_table(
        "finance_exec_intents",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("portfolio_id", sa.String(80), nullable=True),
        sa.Column("instrument_kind", sa.String(10), nullable=True),
        sa.Column("symbol", sa.String(20), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("side", sa.String(4), nullable=True),
        sa.Column("order_type", sa.String(10), nullable=True),
        sa.Column("quantity", QUANTITY, nullable=True),
        sa.Column("limit_price", PRICE, nullable=True),
        sa.Column("amount", MONEY, nullable=True),
        sa.Column("account_id", sa.String(80), nullable=True),
        sa.Column("live_profile", postgresql.JSONB(), nullable=True),
        sa.Column("requested_by", sa.String(10), nullable=False),
        sa.Column("proposer", sa.String(160), nullable=False),
        sa.Column("reason", sa.String(300), nullable=True),
        sa.Column("idempotency_key", sa.String(80), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("decision", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("guardian_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimate", postgresql.JSONB(), nullable=True),
        sa.Column("refusal_code", sa.String(40), nullable=True),
        sa.Column("refusal", sa.String(400), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("approved_at", TS, nullable=True),
        sa.Column("approved_by", sa.String(160), nullable=True),
        sa.Column("executed_at", TS, nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("finance_exec_intents"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "idempotency_key", name="uq_finance_exec_intents_key"),
        sa.CheckConstraint("mode in ('paper', 'shadow', 'live', 'settings')", name="ck_finance_exec_intents_mode"),
        sa.CheckConstraint(
            "kind in ('trade', 'transfer', 'spend', 'bill_payment', 'live_profile')", name="ck_finance_exec_intents_kind"
        ),
        sa.CheckConstraint("(kind = 'live_profile') = (mode = 'settings')", name="ck_finance_exec_intents_settings"),
        sa.CheckConstraint(
            "status in ('pending_approval', 'denied', 'refused', 'executing', 'executed', 'failed', 'expired', 'rejected')",
            name="ck_finance_exec_intents_status",
        ),
        sa.CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="ck_finance_exec_intents_hash"),
        sa.CheckConstraint("requested_by in ('person', 'agent')", name="ck_finance_exec_intents_by"),
        # An assistant only ever proposes simulated trades: never live, never settings.
        sa.CheckConstraint(
            "requested_by = 'person' or (mode in ('paper', 'shadow') and kind = 'trade')", name="ck_finance_exec_intents_agent"
        ),
        # Only the person who owns the request approves it, and nothing runs unapproved.
        sa.CheckConstraint("approved_by is null or approved_by = owner_user_id", name="ck_finance_exec_intents_approver"),
        sa.CheckConstraint(
            "status not in ('executing', 'executed', 'failed') or (approved_at is not null and approved_by is not null)",
            name="ck_finance_exec_intents_approved",
        ),
        # Live execution is not enabled: a live request is never approved or run.
        sa.CheckConstraint(
            "mode <> 'live' or (status in ('pending_approval', 'denied', 'refused', 'expired', 'rejected') and approved_by is null)",
            name="ck_finance_exec_intents_no_live",
        ),
    )
    _owner_index("finance_exec_intents", "status", "created_at")

    op.create_table(
        "finance_exec_settings",
        *_owned(),
        sa.Column("kill_switch", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("kill_switch_at", TS, nullable=True),
        sa.Column("kill_switch_reason", sa.String(300), nullable=True),
        sa.Column("live_opt_in", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("live_opt_in_at", TS, nullable=True),
        sa.Column("live_limits", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("live_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "owner_user_id", name="pk_finance_exec_settings"),
        _workspace_fk("finance_exec_settings"),
        sa.CheckConstraint("not live_opt_in or live_opt_in_at is not null", name="ck_finance_exec_settings_opt_in"),
    )

    op.create_table(
        "finance_exec_global",
        sa.Column("tenant_id", sa.String(80), primary_key=True),
        sa.Column("engaged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("changed_by", sa.String(160), nullable=True),
        sa.Column("changed_at", TS, nullable=True),
        sa.Column("reason", sa.String(300), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_finance_exec_global_tenant"),
    )

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
        op.execute(
            f"""
            create policy owner_only_{table} on {table}
            as restrictive
            using (
              {OWNER_PREDICATE}
            )
            with check (
              {OWNER_PREDICATE}
            )
            """
        )
    op.execute("alter table finance_exec_global enable row level security")
    op.execute("alter table finance_exec_global force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_finance_exec_global on finance_exec_global
        using (
          {TENANT_PREDICATE}
        )
        with check (
          {TENANT_PREDICATE}
        )
        """
    )


def downgrade() -> None:
    op.drop_table("finance_exec_global")
    for table in reversed(TABLES):
        op.drop_table(table)
