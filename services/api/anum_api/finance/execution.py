"""The execution gateway's records, stage 7 (docs/finance.md#execution-gateway).

Kept apart from ``gateway.py`` so the store can hold them without an import cycle.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from .guardian import ActionKind
from .live import LiveLimits
from .wider import WiderSettings
from .models import _Owned
from .trading import InstrumentKind, OrderType, Side


class ExecMode(StrEnum):
    PAPER = "paper"
    SHADOW = "shadow"
    LIVE = "live"
    SETTINGS = "settings"  # a change of the person's live limits or opt-in (stage 8)


class IntentKind(StrEnum):
    TRADE = "trade"
    TRANSFER = "transfer"
    SPEND = "spend"
    BILL_PAYMENT = "bill_payment"
    LIVE_PROFILE = "live_profile"


class IntentStatus(StrEnum):
    PENDING = "pending_approval"
    DENIED = "denied"  # the Guardian gate said no: nothing ran
    REFUSED = "refused"  # the gateway could not run it (no price, live not enabled)
    EXECUTING = "executing"  # claimed once, the executor is running
    EXECUTED = "executed"
    FAILED = "failed"
    EXPIRED = "expired"
    REJECTED = "rejected"


GUARDIAN_KIND = {
    IntentKind.TRADE: ActionKind.TRADE, IntentKind.TRANSFER: ActionKind.TRANSFER, IntentKind.SPEND: ActionKind.SPEND,
    IntentKind.BILL_PAYMENT: ActionKind.BILL_PAYMENT,
}


class Intent(_Owned):
    """One request to act, waiting for (or past) the person's approval. Never edited once made."""

    id: str
    mode: ExecMode
    kind: IntentKind
    portfolio_id: str | None = None
    instrument_kind: InstrumentKind | None = None
    symbol: str | None = None
    currency: str | None = None
    side: Side | None = None
    order_type: OrderType | None = None
    quantity: Decimal | None = None
    limit_price: Decimal | None = None
    amount: Decimal | None = None
    account_id: str | None = None
    live_profile: dict[str, Any] | None = None
    requested_by: Literal["person", "agent"] = "person"
    proposer: str
    reason: str | None = None
    idempotency_key: str
    payload_hash: str
    status: IntentStatus
    decision: dict[str, Any] = Field(default_factory=dict)
    guardian_version: int = 0
    estimate: dict[str, Any] | None = None
    refusal_code: str | None = None
    refusal: str | None = None
    created_at: datetime
    expires_at: datetime
    approved_at: datetime | None = None
    approved_by: str | None = None
    executed_at: datetime | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime

    def lapsed(self, now: datetime) -> bool:
        return self.status == IntentStatus.PENDING and now >= self.expires_at


class ExecSettings(BaseModel):
    """The person's own execution settings: their kill switch and their live limits and opt-in."""

    kill_switch: bool = False
    kill_switch_at: datetime | None = None
    kill_switch_reason: str | None = None
    live_opt_in: bool = False
    live_opt_in_at: datetime | None = None
    live_limits: LiveLimits = Field(default_factory=LiveLimits)
    live_version: int = 0
    # Stage 9: the person's tier above the stage 8 limits, and whether widening is frozen.
    wider: WiderSettings = Field(default_factory=WiderSettings)
    updated_at: datetime | None = None


class GlobalSwitch(BaseModel):
    """The organisation's kill switch (one per tenant)."""

    engaged: bool = False
    changed_by: str | None = None
    changed_at: datetime | None = None
    reason: str | None = None
