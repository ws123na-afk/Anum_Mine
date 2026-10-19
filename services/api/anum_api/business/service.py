"""Business rules shared by the routes and the tools (docs/business.md).

Pure where it can be: normalization and duplicate keys, the deterministic lead score, the
lead state machine, proposal totals and the content hash an acceptance approval binds.
The rest records the activity trail and the audit log and checks the tenant's contact
suppression list (``anum_api/suppression.py``) before anything is drafted or recorded as
a contact.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException

from .. import suppression
from ..audit import AuditRecord
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from .models import (
    CONTACTABLE_BASES,
    CONVERSATION_CRITERIA,
    LEAD_TRANSITIONS,
    STATE_STAMP,
    Activity,
    Contact,
    Criterion,
    CriterionScore,
    Lead,
    LeadState,
    ProposalContent,
    money,
)
from .store import BusinessStore, Query

logger = logging.getLogger("anum.business")

ACCEPT_TOOL = "business.accept_proposal"


def http_error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


# -- normalization and duplicate keys -------------------------------------------------------


_SPACES = re.compile(r"\s+")
_COMPANY_SUFFIXES = re.compile(
    r"\b(llc|l\.l\.c|ltd|limited|inc|co|company|fze|fzc|fz-llc|est|establishment|trading|group|plc|wll|w\.l\.l)\b\.?",
    re.IGNORECASE,
)


def name_key(name: str) -> str:
    """A company name folded for duplicate detection: case, accents, punctuation and legal
    suffixes ("LLC", "FZE", "Trading") do not make two names different."""
    folded = unicodedata.normalize("NFKD", name).casefold()
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    folded = _COMPANY_SUFFIXES.sub(" ", folded)
    folded = re.sub(r"[^\w\s]", " ", folded)
    return _SPACES.sub(" ", folded).strip()[:200]


def clean_url(value: str | None) -> str | None:
    """An http(s) URL without credentials or fragment, or None when it is not one."""
    text = (value or "").strip()
    if not text:
        return None
    if "://" not in text:
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", text) and not re.match(r"^[^/]+:\d+(/|$)", text):
            return None  # another scheme (javascript:, mailto:, data:)
        text = "https://" + text
    try:
        parts = urlsplit(text)
        parts.port  # noqa: B018 - raises on a malformed port
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        return None
    if len(text) > 2048:
        return None
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, parts.query, ""))


def domain_of(url: str | None) -> str | None:
    cleaned = clean_url(url)
    if cleaned is None:
        return None
    host = (urlsplit(cleaned).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host or None


def url_key(url: str) -> str:
    """The same page whatever its case of host, trailing slash or tracking query."""
    parts = urlsplit(url)
    query = "&".join(item for item in parts.query.split("&") if item and not item.lower().startswith(("utm_", "fbclid", "gclid")))
    host = (parts.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    return f"{host}{parts.path.rstrip('/')}{'?' + query if query else ''}"[:2048]


def normal_email(value: str | None) -> str | None:
    found = suppression.normalize(value or "") if value and "@" in value else None
    return found[1] if found else None


def normal_phone(value: str | None) -> str | None:
    if not value or "@" in value:
        return None
    found = suppression.normalize(value)
    return found[1] if found else None


# -- lead score and state machine ----------------------------------------------------------------


def score_totals(score: dict[str, CriterionScore]) -> tuple[int, int]:
    """(sum of known criteria, how many are known). Unknown criteria add nothing and stay unknown."""
    known = [item.score for key, item in score.items() if key in Criterion.__members__.values() and item.score is not None]
    return int(sum(known)), len(known)


def apply_score(lead: Lead, updates: dict[Criterion, CriterionScore], actor: str, now: datetime) -> list[str]:
    """Set criteria; returns the changed criterion names."""
    changed: list[str] = []
    for criterion, value in updates.items():
        before = lead.score.get(criterion.value)
        value = value.model_copy(update={"scored_by": actor, "scored_at": now})
        if before is None or (before.score, before.reason, before.evidence_ids) != (value.score, value.reason, value.evidence_ids):
            lead.score[criterion.value] = value
            changed.append(criterion.value)
    lead.score_total, lead.score_known = score_totals(lead.score)
    return changed


def qualification_gaps(lead: Lead, evidence_count: int) -> list[str]:
    """Why a lead cannot be qualified yet: qualifying needs factual grounds, not a guess."""
    gaps: list[str] = []
    fit = lead.score.get(Criterion.INDUSTRY_FIT.value)
    problem = lead.score.get(Criterion.PROBLEM_EVIDENCE.value)
    if fit is None or fit.score is None:
        gaps.append("Score industry fit first.")
    if problem is None or problem.score is None:
        gaps.append("Score the problem evidence first.")
    elif problem.score > 0 and not problem.evidence_ids:
        gaps.append("Link the evidence that shows the problem.")
    if evidence_count == 0:
        gaps.append("Add at least one source (evidence) for this lead.")
    return gaps


def can_move(current: LeadState, target: LeadState) -> bool:
    return target in LEAD_TRANSITIONS[current]


def stamp_state(lead: Lead, target: LeadState, now: datetime) -> None:
    lead.state = target
    stamp = STATE_STAMP.get(target)
    if stamp and getattr(lead, stamp) is None:
        setattr(lead, stamp, now)


def research_may_score(criterion: Criterion) -> bool:
    return criterion not in CONVERSATION_CRITERIA


# -- suppression and consent -------------------------------------------------------------------


def contact_suppressed(context: TenantContext, contact: Contact | None) -> list[str]:
    """Masked addresses of the contact that are on the tenant's opt-out list (any purpose)."""
    if contact is None:
        return []
    values = [value for value in (contact.email, contact.phone) if value]
    if not values:
        return []
    found = [(kind, value) for kind, value in (suppression.normalize(value) or ("", "") for value in values) if kind]
    if not found:
        return []
    hashes = {suppression.recipient_hash(context.tenant_id, value): (kind, value) for kind, value in found}
    with suppression.open_suppression_store(context) as store:
        entries = store.find(context, list(hashes))
    return [entry.masked for entry in entries]


def contact_block(context: TenantContext, contact: Contact | None, account_dnc: bool) -> str | None:
    """Why this contact must not be contacted, or None."""
    if account_dnc:
        return "The account is marked do not contact."
    if contact is None:
        return None
    if contact.do_not_contact:
        return f"{contact.name} is marked do not contact."
    masked = contact_suppressed(context, contact)
    if masked:
        return f"{', '.join(masked)} is on the contact opt-out list."
    return None


def consent_missing(contact: Contact | None) -> str | None:
    if contact is None:
        return "Add the contact you reached first."
    if contact.consent_basis not in CONTACTABLE_BASES:
        return f"Record how ANUM may contact {contact.name} (consent basis) first; unknown stays unknown."
    return None


# -- proposals -------------------------------------------------------------------------------


def proposal_totals(content: ProposalContent) -> dict[str, Any]:
    """Subtotal, tax, total, margin and the milestone check, all reconciled in Decimal.

    ``issues`` block sending it for acceptance; ``warnings`` are shown but do not."""
    subtotal = money(sum((item.amount() for item in content.line_items), Decimal("0")))
    external = money(sum((item.amount() for item in content.line_items if item.kind.value == "external_cost"), Decimal("0")))
    tax = money(subtotal * content.tax_rate_pct / Decimal(100))
    total = money(subtotal + tax)
    milestones = money(sum((item.amount for item in content.milestones), Decimal("0")))
    margin = money(subtotal - content.internal_cost)
    issues: list[str] = []
    if not content.line_items:
        issues.append("Add at least one line item.")
    if content.milestones and milestones != total:
        issues.append(f"Milestones add up to {milestones} but the total is {total}.")
    if not content.deliverables:
        issues.append("List the deliverables.")
    if not content.acceptance_criteria:
        issues.append("State how the client accepts the work (acceptance criteria).")
    warnings: list[str] = []
    if content.internal_cost > subtotal and subtotal > 0:
        warnings.append("The internal cost estimate is above the price: the margin is negative.")
    if content.tax_rate_pct > 0 and not content.tax_note.strip():
        warnings.append("Say whether the tax is confirmed for the client's jurisdiction.")
    return {
        "subtotal": subtotal,
        "external_costs": external,
        "tax": tax,
        "total": total,
        "milestone_total": milestones,
        "margin": margin,
        "margin_pct": (money(margin * Decimal(100) / subtotal) if subtotal > 0 else None),
        "issues": issues,
        "warnings": warnings,
    }


def content_hash(content: ProposalContent) -> str:
    """SHA-256 of the canonical JSON of what the proposal says."""
    document = ProposalContent.model_validate(content.model_dump()).model_dump(mode="json")
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def draft_hash(subject: str, body: str, channel: str, contact_id: str | None) -> str:
    encoded = json.dumps([subject, body, channel, contact_id], ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# -- activity trail and audit ------------------------------------------------------------------


def record_activity(
    store: BusinessStore,
    context: TenantContext,
    kind: str,
    summary: str,
    *,
    lead_id: str | None = None,
    account_id: str | None = None,
    opportunity_id: str | None = None,
    proposal_id: str | None = None,
    project_id: str | None = None,
    ticket_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    actor: str | None = None,
) -> tuple[Activity, bool]:
    """One trail entry; with a key, the same key returns the first entry (no duplicate)."""
    if idempotency_key:
        existing = store.first("activities", idempotency_key=idempotency_key)
        if existing is not None:
            return existing, False
    activity = Activity(
        id=new_id("bact"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, lead_id=lead_id, account_id=account_id,
        opportunity_id=opportunity_id, proposal_id=proposal_id, project_id=project_id, ticket_id=ticket_id, kind=kind, summary=summary[:500],
        metadata=metadata or {}, actor=actor or context.user_id, idempotency_key=idempotency_key, created_at=utc_now(),
    )
    store.add("activities", activity)
    return activity, True


def audit(
    repository: AnumRepository | None,
    context: TenantContext,
    action: str,
    target: str,
    *,
    outcome: str = "success",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Audit log entry with ids and counts only: never contact details or message text."""
    record = AuditRecord(
        id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id, action=action,
        target=target, outcome=outcome, correlation_id=target, created_at=utc_now(), metadata=metadata or {},
    )
    if repository is not None:
        repository.record_audit(record)
        return
    from ..dependencies import _unit_of_work

    try:
        with _unit_of_work(context, require_membership=False) as detached:
            detached.record_audit(record)
    except Exception:  # an audit failure must not undo a change that already committed
        logger.warning("business_audit_failed", extra={"anum_business": {"event": "audit_failed", "action": action}})


def today_window(now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, now


def usage_today(store: BusinessStore, now: datetime) -> tuple[int, int]:
    """(research runs, web searches) used today (UTC) in this workspace."""
    start, _ = today_window(now)
    runs, _ = store.find("research_runs", Query(created_from=start, limit=1000, order_by="created_at"))
    return len(runs), sum(run.queries for run in runs)
