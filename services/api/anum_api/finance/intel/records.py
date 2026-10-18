"""Stored trading intelligence records (migration ``0065_finance_intel``, docs/finance-trading-intel.md#storage).

* ``StrategyConfig``: one **version** of a strategy: rule key, code version, symbols and
  parameters, its lifecycle state and why it is in it. A version is never edited after it
  leaves DRAFT: a change is a new version in the same lineage (``lifecycle.new_version``).
* ``StrategyRun``: one backtest or walk-forward evaluation of a version, with the SHA-256 of
  the exact bars it used, the costs, metrics, verdict and reasons. SIMULATED.
* ``AgentDecision``: one structured output of the Trading Supervisor or a specialist agent
  (or the person's own lifecycle action), with the strategy and execution intent it concerns.

Every row is the owner's own (tenant, workspace and ``owner_user_id``), like the rest of
Finance.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from ..models import _Owned

MAX_REASON = 300
MAX_STRATEGIES = 200
MAX_RUNS = 2_000  # per owner, every strategy together
MAX_DECISIONS = 20_000
MAX_INSTRUMENTS = 200
MAX_BARS = 50_000  # per instrument


class StrategyState(StrEnum):
    DRAFT = "DRAFT"
    BACKTESTING = "BACKTESTING"
    VALIDATION = "VALIDATION"
    PAPER_ACTIVE = "PAPER_ACTIVE"
    PAPER_SUSPENDED = "PAPER_SUSPENDED"
    RETIRED = "RETIRED"


class Agent(StrEnum):
    """Who decided. ``person`` is the owner acting themselves; the rest are ANUM's agents."""

    PERSON = "person"
    SUPERVISOR = "trading_supervisor"
    MARKET_RESEARCH = "market_research"
    TECHNICAL_ANALYSIS = "technical_analysis"
    STRATEGY_RESEARCH = "strategy_research"
    PORTFOLIO = "portfolio"
    RISK_GUARDIAN = "risk_guardian"
    EXECUTION_COORDINATOR = "execution_coordinator"
    PERFORMANCE_REVIEWER = "performance_reviewer"


class StrategyConfig(_Owned):
    id: str
    lineage_id: str  # every version of one strategy shares it
    version: int = Field(ge=1)
    key: str  # the rule in strategies.REGISTRY
    code_version: str  # the rule's own version when this config was made
    name: str = Field(max_length=120)
    state: StrategyState = StrategyState.DRAFT
    symbols: list[str]
    params: dict[str, Any] = Field(default_factory=dict)
    instrument_ids: list[str] = Field(default_factory=list)  # the stored instruments the symbols come from
    portfolio_id: str | None = None  # the paper portfolio it trades in once PAPER_ACTIVE
    reason: str = Field(max_length=MAX_REASON)  # why this version exists, or why it is in its state
    parent_id: str | None = None
    evidence_run_id: str | None = None  # the run that justified this version (required for an agent's version)
    created_by: str  # "person" or an Agent value
    created_at: datetime
    updated_at: datetime
    state_changed_at: datetime
    state_changed_by: str = Agent.PERSON.value  # who made the last state change (the database refuses an agent for PAPER_ACTIVE)


class StrategyRun(_Owned):
    id: str
    strategy_id: str
    kind: Literal["backtest", "walk_forward"]
    created_at: datetime
    data_source: str  # e.g. "csv_import", "binance_public", or the synthetic label
    data_hash: str  # SHA-256 over every bar used (validation.data_hash)
    code_version: str
    symbols: list[str]
    params: dict[str, Any] = Field(default_factory=dict)
    costs: dict[str, str] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    benchmark: dict[str, Any] | None = None
    verdict: Literal["passed", "rejected", "insufficient_data", "completed"]
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    folds: list[dict[str, Any]] = Field(default_factory=list)
    reproduced: bool = False  # re-run on the same bars gave the same hash and metrics
    report: dict[str, Any] = Field(default_factory=dict)
    requested_by: str = Agent.PERSON.value


class AgentDecision(_Owned):
    id: str
    cycle_id: str | None = None  # one Supervisor cycle groups its agents' decisions
    agent: Agent
    action: str = Field(max_length=60)
    outcome: str = Field(max_length=30)  # ok, proposed, refused, suspended, skipped, failed, ...
    reasons: list[str] = Field(default_factory=list)
    strategy_id: str | None = None
    intent_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
