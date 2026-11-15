"""What the matching engine decided and the backfill runs (migration ``0184_pf_matching``, docs/finance-matching.md).

The rows live with the finance links (``links_store``): the in-memory store and
``db.finance_links_repository.SqlAlchemyLinkStore`` keep them, so the privacy export, the count and
"Delete my data" cover them with the links, in the owner's own scope (forced RLS, owner-only).

A **match record** is one decision about one pair: a canonical transaction and one obligation target (a bill's due
date, a Tamara installment's bill, a card or a bank loan). It keeps the evidence, the policy version, the reason code
and the confidence, the link it made (if any) and the owner's later choice. There is at most one per pair, so a
rerun updates the record instead of adding one, and an owner's choice (accepted, rejected, undone) is never
overwritten by the engine.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

Outcome = Literal["auto_linked", "review", "accepted", "rejected", "undone", "stale"]
EvidenceState = Literal["pending", "provisional", "verified"]
# The owner decided: the engine never changes these again (it may only report on them).
OWNER_OUTCOMES = frozenset({"accepted", "rejected", "undone"})
RunMode = Literal["preview", "apply"]
RunStatus = Literal["running", "done", "failed", "undone"]
COUNT_KEYS = ("transactions", "matched", "auto_linked", "verified", "provisional", "ambiguous", "review", "missing_source", "duplicates",
              "corrected", "differences", "already_linked", "kept_owner_choice", "errors")


class EvidenceItem(BaseModel):
    """One fact behind a decision: a code the clients translate and a short value (never a message body)."""

    code: str = Field(max_length=40)
    value: str | None = Field(default=None, max_length=120)
    weight: Decimal = Decimal(0)


class MatchRecord(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    canonical_id: str
    kind: str  # installment, bill, card_repayment, bank_loan
    dimension: str  # the link dimension: bill or debt
    target_type: str
    target_id: str
    label: str = Field(default="", max_length=160)  # the obligation's name, as the owner named it
    due_on: date | None = None
    posted_on: date | None = None
    amount: Decimal
    currency: str
    outcome: Outcome
    evidence_state: EvidenceState = "pending"
    reason_code: str = Field(max_length=40)
    confidence: Decimal
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=30)
    flags: list[str] = Field(default_factory=list, max_length=10)  # refund_signal, source_changed, duplicate_event
    competitors: int = 0  # other candidates at review level
    policy_version: str = Field(max_length=40)
    run_id: str | None = None
    link_id: str | None = None
    fingerprint: str = Field(default="", max_length=64)
    # Across currencies (0188_pf_fx_matching): ``amount``/``currency`` are the obligation's side; this holds the bank side
    # (``bank_amount``, ``bank_currency``), the original amount, the rate and its kind and source, and the FX fee kept apart.
    fx: dict[str, Any] | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    version: int = 1


class BackfillRun(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    mode: RunMode
    status: RunStatus = "running"
    start_on: date | None = None  # the oldest transaction found when the run began
    end_on: date | None = None  # the newest
    cursor_on: date | None = None  # the last month finished (checkpoint): a resumed run goes on after it
    batches: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    policy_version: str = Field(max_length=40)
    error_code: str | None = Field(default=None, max_length=40)
    attempts: int = 0
    created_by: str
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    undone_at: datetime | None = None
    version: int = 1


__all__ = ["COUNT_KEYS", "OWNER_OUTCOMES", "BackfillRun", "EvidenceItem", "EvidenceState", "MatchRecord", "Outcome", "RunMode", "RunStatus"]
