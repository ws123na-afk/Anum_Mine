"""Customer decisions recorded in the portal, as the owner's views read them (docs/business-portal.md#decisions)."""

from __future__ import annotations

from .invoice_models import DecisionSubject, PortalDecision, PortalDecisionSummary
from .store import BusinessStore, Query


def latest_decision(store: BusinessStore, kind: DecisionSubject, subject_id: str) -> PortalDecision | None:
    items, _ = store.find("portal_decisions", Query(where={"subject_kind": kind, "subject_id": subject_id}, order_by="decided_at", limit=1))
    return items[0] if items else None


def decision_summary(store: BusinessStore, kind: DecisionSubject, subject_id: str, current_hash: str | None) -> PortalDecisionSummary | None:
    found = latest_decision(store, kind, subject_id)
    if found is None:
        return None
    return PortalDecisionSummary(decision=found.decision, signer_name=found.signer_name, note=found.note, decided_at=found.decided_at, version=found.version,
                                 content_hash=found.content_hash, current=bool(current_hash) and found.content_hash == current_hash)
