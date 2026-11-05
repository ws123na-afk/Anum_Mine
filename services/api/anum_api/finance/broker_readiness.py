"""Broker onboarding checks and the live eligibility preflight (docs/finance.md#live-eligibility-preflight).

This is the one place that answers "what is still missing before live trading could
ever be considered". It NEVER stores funds, handles credentials, opens accounts or
sends orders. It separates broker custody and read-only observation from possible
execution. All live-execution requirements remain enforced by finance.live and the
gateway; nothing here can change them.

The facts are typed by a person and are not evidence: even a complete checklist
leaves live trading off and sends no order.
"""
from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .live import Precondition


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
    broker_country_eligibility_verified: bool = False
    asset_permissions_verified: bool = False
    funding_only_at_broker: bool = False
    market_data_terms_accepted: bool = False
    data_freshness_verified: bool = False
    read_only_access_verified: bool = False
    historical_data_tests_passed: bool = False
    paper_testing_passed: bool = False
    guardian_regression_passed: bool = False
    reconciliation_tests_passed: bool = False
    independent_security_review_passed: bool = False
    written_live_approval: bool = False
    withdrawal_scope_absent: bool = False
    credentials_in_secret_store: bool = False
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
    "broker_country_eligibility_verified",
    "asset_permissions_verified",
    "funding_only_at_broker",
    "market_data_terms_accepted",
    "data_freshness_verified",
    "read_only_access_verified",
)
RESEARCH_GATE = ("historical_data_tests_passed", "paper_testing_passed", "guardian_regression_passed")
LIVE_REVIEW_GATE = (
    "reconciliation_tests_passed",
    "independent_security_review_passed",
    "written_live_approval",
    "withdrawal_scope_absent",
    "credentials_in_secret_store",
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


# -- live eligibility preflight ------------------------------------------------------------------


class BrokerKind(StrEnum):
    ALPACA = "alpaca"
    INTERACTIVE_BROKERS = "interactive_brokers"
    OTHER = "other"


class AssetKind(StrEnum):
    STOCK = "stock"
    CRYPTO = "crypto"


_COUNTRY = re.compile(r"^[A-Z]{2}$")


class BrokerProfile(BaseModel):
    """Which broker, where the account holder lives and which assets. No account numbers or keys."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    broker: BrokerKind
    country_of_residence: str = Field(min_length=2, max_length=2)
    assets: list[AssetKind] = Field(min_length=1, max_length=len(AssetKind))

    @field_validator("country_of_residence", mode="before")
    @classmethod
    def _country(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip().upper()
            if not _COUNTRY.match(value):
                raise ValueError("Use the two-letter country code, for example SA or AE.")
        return value

    @model_validator(mode="after")
    def _unique_assets(self) -> "BrokerProfile":
        if len(set(self.assets)) != len(self.assets):
            raise ValueError("Each asset class can be listed once.")
        return self


class InfoNote(BaseModel):
    """Something to check that the checklist cannot settle, with its public source."""

    code: str
    source: str


# Saudi residents and crypto: the Saudi Central Bank (SAMA) and the Capital Market
# Authority (CMA) state, in the standing committee's warning of 12 August 2018, that
# virtual currencies are not regulated in the Kingdom and that no party is licensed by
# the regulators to deal in them. Re-check before relying on it (docs/finance.md).
SA_CRYPTO_SOURCE = "https://cma.gov.sa/en/MediaCenter/PR/Pages/Virtual_Currencies.aspx"


def informational_notes(profile: BrokerProfile | None) -> list[InfoNote]:
    if profile is None:
        return []
    notes: list[InfoNote] = []
    if profile.country_of_residence == "SA" and AssetKind.CRYPTO in profile.assets:
        notes.append(InfoNote(code="sa_crypto_not_licensed", source=SA_CRYPTO_SOURCE))
    return notes


class LivePreflightResult(BaseModel):
    """What is still missing before live could ever be considered. Live stays off."""

    model_config = ConfigDict(frozen=True)

    stage: IntegrationStage
    missing_data_requirements: list[str]
    missing_research_requirements: list[str]
    missing_live_review_requirements: list[str]
    system_preconditions: list[Precondition]
    # For Interactive Brokers: the IBKR connector's own live gates for this person, read only
    # (docs/finance-live-trading.md#locks). Empty for other brokers or without a profile.
    broker_gates: list[Precondition] = Field(default_factory=list)
    notes: list[InfoNote]
    broker: BrokerKind | None = None
    assets: list[AssetKind] = Field(default_factory=list)
    eligibility_confirmed: Literal[False] = False
    live_trading_enabled: Literal[False] = False
    credentials_connected: Literal[False] = False
    deposits_into_anum_supported: Literal[False] = False
    orders_sent: Literal[0] = 0
    explanation: str


PREFLIGHT_EXPLANATION = (
    "Live trading is off and this check cannot turn it on. No broker account is connected, "
    "no key is read and no order is sent. Funds stay at the owner's broker, never in ANUM. "
    "Ticked items are your own notes, not evidence."
)


def evaluate_live_preflight(
    facts: BrokerReadiness, profile: BrokerProfile | None, system: list[Precondition],
    broker_gates: list[Precondition] | None = None,
) -> LivePreflightResult:
    """The checklist, the server's own live preconditions and informational notes, in one answer.

    ``system`` comes from ``gateway.preconditions`` (read only). Whatever the inputs say,
    the answer keeps live trading off and reports zero orders sent.
    """
    checklist = evaluate_broker_readiness(facts)
    return LivePreflightResult(
        stage=checklist.stage,
        missing_data_requirements=checklist.missing_data_requirements,
        missing_research_requirements=checklist.missing_research_requirements,
        missing_live_review_requirements=checklist.missing_live_review_requirements,
        system_preconditions=list(system),
        broker_gates=list(broker_gates or []) if profile and profile.broker == BrokerKind.INTERACTIVE_BROKERS else [],
        notes=informational_notes(profile),
        broker=profile.broker if profile else None,
        assets=list(profile.assets) if profile else [],
        explanation=PREFLIGHT_EXPLANATION,
    )
