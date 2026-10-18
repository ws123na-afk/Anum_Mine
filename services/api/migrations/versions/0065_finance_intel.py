"""Finance trading intelligence: market data, strategy versions, runs and agent decisions
(docs/finance-trading-intel.md#storage). Everything here is research and SIMULATION.

* ``finance_instruments``: the person's instrument master (symbol, asset class, venue,
  currency, provider symbols, listing dates). One row per symbol and venue.
* ``finance_market_bars``: daily OHLCV bars with their source and fetch time, one per
  instrument and day. Checks: positive prices, the high and low contain the open and close.
* ``finance_strategies``: one row per strategy **version**, unique per lineage and version.
  Checks: the six lifecycle states only (no live state), a PAPER_ACTIVE version has a paper
  portfolio, and only the person (``state_changed_by = 'person'``) puts a version into
  PAPER_ACTIVE or RETIRED; agents may only research and suspend.
* ``finance_strategy_runs``: backtest and walk-forward evaluations with the SHA-256 of the
  bars used, costs, metrics, verdict and reasons.
* ``finance_agent_decisions``: the Trading Supervisor's and specialist agents' structured
  outputs (and the person's lifecycle actions), with the strategy and intent they concern.

Every table belongs to one tenant and workspace (foreign key to ``workspaces``) under forced
RLS with ``tenant_isolation_<table>`` and a restrictive ``owner_only_<table>`` policy, like
0056 to 0059. No role is granted anything new.

Expand only: new tables. The previous release ignores them.

Chained after ``0064_mail_operator_view``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0065_finance_intel"
down_revision = "0064_mail_operator_view"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"
TABLES = ("finance_instruments", "finance_market_bars", "finance_strategies", "finance_strategy_runs", "finance_agent_decisions")
TS = sa.DateTime(timezone=True)
PRICE = sa.Numeric(24, 10)
STATES = "('DRAFT', 'BACKTESTING', 'VALIDATION', 'PAPER_ACTIVE', 'PAPER_SUSPENDED', 'RETIRED')"
AGENTS = (
    "('person', 'trading_supervisor', 'market_research', 'technical_analysis', 'strategy_research', 'portfolio', "
    "'risk_guardian', 'execution_coordinator', 'performance_reviewer')"
)


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


def _json(name: str, default: str = "'{}'::jsonb", nullable: bool = False) -> sa.Column:
    return sa.Column(name, postgresql.JSONB(), nullable=nullable, server_default=None if nullable else sa.text(default))


def upgrade() -> None:
    op.create_table(
        "finance_instruments",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("name", sa.String(120), nullable=True),
        sa.Column("asset_class", sa.String(12), nullable=False),
        sa.Column("venue", sa.String(10), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False),
        _json("provider_symbols"),
        sa.Column("listed_on", sa.Date(), nullable=True),
        sa.Column("delisted_on", sa.Date(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("finance_instruments"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "symbol", "venue", name="uq_finance_instruments_symbol"),
        sa.CheckConstraint("asset_class in ('equity', 'etf', 'crypto', 'forex', 'commodity')", name="ck_finance_instruments_class"),
        sa.CheckConstraint("delisted_on is null or listed_on is null or delisted_on >= listed_on", name="ck_finance_instruments_dates"),
    )

    op.create_table(
        "finance_market_bars",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("instrument_id", sa.String(80), sa.ForeignKey("finance_instruments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("open", PRICE, nullable=False),
        sa.Column("high", PRICE, nullable=False),
        sa.Column("low", PRICE, nullable=False),
        sa.Column("close", PRICE, nullable=False),
        sa.Column("volume", sa.Numeric(28, 8), nullable=True),
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("delayed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("fetched_at", TS, nullable=False),
        _workspace_fk("finance_market_bars"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "instrument_id", "day", name="uq_finance_market_bars_day"),
        sa.CheckConstraint("low > 0 and high >= low and high >= open and high >= close and low <= open and low <= close",
                           name="ck_finance_market_bars_prices"),
        sa.CheckConstraint("volume is null or volume >= 0", name="ck_finance_market_bars_volume"),
    )

    op.create_table(
        "finance_strategies",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("lineage_id", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("code_version", sa.String(20), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        _json("symbols", "'[]'::jsonb"),
        _json("params"),
        _json("instrument_ids", "'[]'::jsonb"),
        sa.Column("portfolio_id", sa.String(80), nullable=True),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("parent_id", sa.String(80), nullable=True),
        sa.Column("evidence_run_id", sa.String(80), nullable=True),
        sa.Column("created_by", sa.String(40), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("state_changed_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("state_changed_by", sa.String(40), nullable=False),
        _workspace_fk("finance_strategies"),
        sa.UniqueConstraint("lineage_id", "version", name="uq_finance_strategies_version"),
        sa.CheckConstraint(f"state in {STATES}", name="ck_finance_strategies_state"),
        sa.CheckConstraint("version >= 1", name="ck_finance_strategies_version"),
        sa.CheckConstraint(f"created_by in {AGENTS} and state_changed_by in {AGENTS}", name="ck_finance_strategies_actor"),
        # Only the person promotes to paper trading or retires; agents research and suspend.
        sa.CheckConstraint("state not in ('PAPER_ACTIVE', 'RETIRED') or state_changed_by = 'person'", name="ck_finance_strategies_person"),
        sa.CheckConstraint("state <> 'PAPER_ACTIVE' or portfolio_id is not null", name="ck_finance_strategies_portfolio"),
        # An agent's version names the run that justifies it.
        sa.CheckConstraint("created_by = 'person' or version = 1 or evidence_run_id is not null", name="ck_finance_strategies_evidence"),
    )
    op.create_index("ix_finance_strategies_owner", "finance_strategies", ["tenant_id", "workspace_id", "owner_user_id", "state", "updated_at"])

    op.create_table(
        "finance_strategy_runs",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("strategy_id", sa.String(80), sa.ForeignKey("finance_strategies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(14), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("data_source", sa.String(120), nullable=False),
        sa.Column("data_hash", sa.String(64), nullable=False),
        sa.Column("code_version", sa.String(20), nullable=False),
        _json("symbols", "'[]'::jsonb"),
        _json("params"),
        _json("costs"),
        _json("metrics"),
        _json("benchmark", nullable=True),
        sa.Column("verdict", sa.String(20), nullable=False),
        _json("reasons", "'[]'::jsonb"),
        _json("warnings", "'[]'::jsonb"),
        _json("folds", "'[]'::jsonb"),
        sa.Column("reproduced", sa.Boolean(), nullable=False, server_default=sa.false()),
        _json("report"),
        sa.Column("requested_by", sa.String(40), nullable=False),
        _workspace_fk("finance_strategy_runs"),
        sa.CheckConstraint("kind in ('backtest', 'walk_forward')", name="ck_finance_strategy_runs_kind"),
        sa.CheckConstraint("verdict in ('passed', 'rejected', 'insufficient_data', 'completed')", name="ck_finance_strategy_runs_verdict"),
        sa.CheckConstraint("data_hash ~ '^[0-9a-f]{64}$'", name="ck_finance_strategy_runs_hash"),
        sa.CheckConstraint(f"requested_by in {AGENTS}", name="ck_finance_strategy_runs_actor"),
    )
    op.create_index(
        "ix_finance_strategy_runs_owner", "finance_strategy_runs", ["tenant_id", "workspace_id", "owner_user_id", "strategy_id", "created_at"]
    )

    op.create_table(
        "finance_agent_decisions",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("cycle_id", sa.String(80), nullable=True),
        sa.Column("agent", sa.String(40), nullable=False),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("outcome", sa.String(30), nullable=False),
        _json("reasons", "'[]'::jsonb"),
        sa.Column("strategy_id", sa.String(80), nullable=True),
        sa.Column("intent_id", sa.String(80), nullable=True),
        _json("details"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("finance_agent_decisions"),
        sa.CheckConstraint(f"agent in {AGENTS}", name="ck_finance_agent_decisions_agent"),
    )
    op.create_index(
        "ix_finance_agent_decisions_owner", "finance_agent_decisions", ["tenant_id", "workspace_id", "owner_user_id", "created_at"]
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


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
