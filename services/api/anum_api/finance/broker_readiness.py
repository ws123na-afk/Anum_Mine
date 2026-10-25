"""Broker onboarding checks for a future ANUM trading integration.

This module NEVER stores funds, handles credentials, opens accounts or sends orders.
It separates broker custody and read-only observation from possible execution.
All live-execution requirements remain enforced by finance.live and the gateway.
"""
from __future__ import annotations

from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field, model_validator


class IntegrationStage(StrEnum):
    NOT_CONNECTED = "not_connected"
    PAPER_ONLY = "paper_only"
    READ_ONLY = "read_only"
    LIVE_BLOCKED = "live_blocked"


class BrokerReadiness(BaseModel):
    """Non-secret onboarding facts provided by a human operator.

    A true field must be supported by independent evidence; this object does not
    perform account verification and cannot grant execution authorization.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    regulated_provider_verified: bool = False
    account_in_owner_name: bool = False
    funding_only_at_broker: bool = False
    market_data_terms_accepted: bool = False
    data_freshness_verified: bool = False
    read_only_access_verified: bool = False
    historical_data_tests_passed: bool = False
    guardian_regression_passed: bool = False
    reconciliation_tests_passed: bool = False
    independent_security_review_passed: bool = False
    written_live_approval: bool = False
    withdrawal_scope_absent: bool = False
    order_idempotency_verified: bool = False
    emergency_stop_verified: bool = False

    @model_validator(mode="after")
    def no_impossible_sequence(self) -> "BrokerReadiness":
        if self.read_only_access_verified and not self.regulated_provider_verified:
            raise ValueError("Read-only broker access cannot be marked verified before the broker.")
        return self


DATA_GATE = (
    "regulated_provider_verified",
    "account_in_owner_name",
    "funding_only_at_broker",
    "market_data_terms_accepted",
    "data_freshness_verified",
    "read_only_access_verified",
)
RESEARCH_GATE = ("historical_data_tests_passed", "guardian_regression_passed")
LIVE_REVIEW_GATE = (
    "reconciliation_tests_passed",
    "independent_security_review_passed",
    "written_live_approval",
    "withdrawal_scope_absent",
    "order_idempotency_verified",
    "emergency_stop_verified",
)


class BrokerReadinessResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    stage: IntegrationStage
    missing_data_requirements: list[str] = Field(default_factory=list)
    missing_research_requirements: list[str] = Field(default_factory=list)
    missing_live_review_requirements: list[str] = Field(default_factory=list)
    live_trading_enabled: bool = False
    deposits_into_anum_supported: bool = False
    explanation: str


def evaluate_broker_readiness(facts: BrokerReadiness) -> BrokerReadinessResult:
    """Fail-closed, informational checklist. NEVER authorizes real trading.

    Even a complete checklist cannot enable live trading. That requires a
    separately implemented and reviewed executor plus existing live gates.
    """
    missing_data = [key for key in DATA_GATE if not getattr(facts, key)]
    missing_research = [key for key in RESEARCH_GATE if not getattr(facts, key)]
    missing_live = [key for key in LIVE_REVIEW_GATE if not getattr(facts, key)]
    if missing_data:
        stage = IntegrationStage.NOT_CONNECTED
    elif missing_research:
        stage = IntegrationStage.READ_ONLY
    else:
        stage = IntegrationStage.LIVE_BLOCKED
    return BrokerReadinessResult(
        stage=stage,
        missing_data_requirements=missing_data,
        missing_research_requirements=missing_research,
        missing_live_review_requirements=missing_live,
        live_trading_enabled=False,
        deposits_into_anum_supported=False,
        explanation=(
            "Funds remain at the owner's regulated broker, never in ANUM. "
            "This checklist cannot place orders or enable live trading. "
            "Paper orders still require Guardian and owner approval."
        ),
    )
