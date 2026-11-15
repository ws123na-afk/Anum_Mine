"""The owner's record-linking policy (docs/finance-matching.md#the-policy).

A part of the owner's automation settings (``AutomationPolicy.matching``, stored with them in
``finance_automation_settings``): which kinds of record linking ANUM may do on its own, the
confidence it needs, and which sources count as proof that money left the account.

Everything is **off** until the owner turns it on and approves the policy, and it links only
debits on the source accounts the owner named. Only low-risk record linking is ever automatic:
a link writes no journal entry, posts no money and can be undone without losing the transaction
(``links.void``). Money movement, payment approval, sensitive adjustments, loans with people,
a bank loan's found-payments batch and policy changes themselves stay with the owner
(``rules.NEVER_AUTOMATED``).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# The engine's rule set. A new version changes how evidence is scored; stored with every decision.
ENGINE_VERSION = "pfm-1"

SourceKind = Literal["statement", "sms", "wallet", "manual"]
LinkKind = Literal["installment", "bill", "card_repayment", "bank_loan"]
# Each kind's switch on the policy (the kind is only linked automatically while its switch is on).
KIND_SWITCH: dict[str, str] = {
    "installment": "reconcile_installments",
    "bill": "link_recurring_bills",
    "card_repayment": "link_card_repayments",
    "bank_loan": "link_bank_loan_repayments",
}


class MatchingPolicy(BaseModel):
    """Per-kind switches, thresholds and the evidence policy. Off by default."""

    model_config = ConfigDict(extra="forbid")

    match_transactions: bool = False  # the master switch: no automatic link at all while off
    reconcile_installments: bool = False  # Tamara (and other provider) installments
    link_recurring_bills: bool = False
    link_card_repayments: bool = False
    link_bank_loan_repayments: bool = False  # one instalment's unique debit; never the found-payments batch
    prevent_duplicates: bool = True  # a payment that looks like a second report of another is never linked on its own
    reprocess_history: bool = False  # the backfill may apply (a preview always works)
    notify_exceptions: bool = False  # an in-app notice when matches wait for review
    min_confidence: Decimal = Field(default=Decimal("0.9"), ge=Decimal("0.8"), le=1)  # auto-link at or above
    review_confidence: Decimal = Field(default=Decimal("0.5"), ge=Decimal("0.3"), le=Decimal("0.79"))  # a candidate at all
    # Which sources prove settlement: a link made on these is "verified"; on others it is "provisional"
    # (it waits as needs_review, so the due date shows pending, never paid or overdue).
    settled_sources: list[SourceKind] = Field(default_factory=lambda: ["statement", "sms"], max_length=4)
    # Debits on these accounts may be linked automatically; empty means review only.
    source_account_ids: list[str] = Field(default_factory=list, max_length=50)
    revision: int = Field(default=0, ge=0)  # bumped on every save; part of the policy version
    approved_at: datetime | None = None  # the owner approved this policy (switches do nothing before)
    approved_by: str | None = Field(default=None, max_length=160)

    @field_validator("settled_sources", "source_account_ids")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))

    @property
    def version(self) -> str:
        return f"{ENGINE_VERSION}.r{self.revision}"

    def automatic(self, kind: str) -> bool:
        """May ANUM link this kind on its own right now?"""
        switch = KIND_SWITCH.get(kind)
        return bool(self.match_transactions and self.approved_at is not None and switch and getattr(self, switch))


__all__ = ["ENGINE_VERSION", "KIND_SWITCH", "LinkKind", "MatchingPolicy", "SourceKind"]
