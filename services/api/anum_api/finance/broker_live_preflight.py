"""Live broker onboarding diagnostics, deliberately not an order executor.

This module prepares broker eligibility and connection verification without
taking deposits, reading secrets from user requests, or turning on execution.
No user-supplied checklist can authorize a live trade.
"""
from __future__ import annotations

from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field, model_validator


class BrokerKind(StrEnum):
    ALPACA = "alpaca"
    INTERACTIVE_BROKERS = "interactive_brokers"


class AssetKind(StrEnum):
    STOCK = "stock"
    CRYPTO = "crypto"


class LivePreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    broker: BrokerKind
    country_of_residence: str = Field(min_length=2, max_length=2)
    assets: list[AssetKind] = Field(min_length=1, max_length=2)
    paper_testing_passed: bool = False
    real_account_approved: bool = False
    broker_country_eligibility_verified: bool = False
    asset_permissions_verified: bool = False
    api_permission_review_passed: bool = False
    secrets_in_tenant_vault: bool = False
    market_data_license_verified: bool = False
    independent_security_review_passed: bool = False
    guardian_independent_test_passed: bool = False
    order_idempotency_test_passed: bool = False
    emergency_stop_test_passed: bool = False
    explicit_written_user_live_approval: bool = False

    @model_validator(mode="after")
    def unique_assets(self):
        if len(set(self.assets)) != len(self.assets):
            raise ValueError("Asset classes cannot repeat")
        return self


class LivePreflightResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    broker: BrokerKind
    residence: str
    assets: list[AssetKind]
    missing: list[str]
    eligibility_confirmed: bool = False
    live_trading_enabled: bool = False
    credentials_connected: bool = False
    funds_held_by_anum: bool = False
    orders_sent: int = 0
    phase: str
    explanation: str


REQUIREMENTS = (
    "paper_testing_passed", "real_account_approved",
    "broker_country_eligibility_verified", "asset_permissions_verified",
    "api_permission_review_passed", "secrets_in_tenant_vault",
    "market_data_license_verified", "independent_security_review_passed",
    "guardian_independent_test_passed", "order_idempotency_test_passed",
    "emergency_stop_test_passed", "explicit_written_user_live_approval",
)


def evaluate_live_preflight(request: LivePreflightRequest) -> LivePreflightResult:
    missing = [requirement for requirement in REQUIREMENTS
               if not getattr(request, requirement)]
    if request.country_of_residence.upper() == "SA" and AssetKind.CRYPTO in request.assets:
        missing.append("independent_sa_crypto_legal_and_provider_verification")
    # Even an entirely satisfied user checklist is not a substitute for trusted
    # connector-side attestations, a tenant credential vault and signed approvals.
    return LivePreflightResult(
        broker=request.broker,
        residence=request.country_of_residence.upper(),
        assets=request.assets,
        missing=missing,
        phase="paper_and_read_only_preparation",
        explanation=(
            "No broker account is connected by this form. Funds stay at the "
            "owner's broker; live deposits, withdrawals and orders remain "
            "disabled. User-entered evidence cannot grant trading permission."
        ),
    )
