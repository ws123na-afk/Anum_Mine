"""The lead research skill (docs/business.md#research): find, profile, score, draft.

1. **Find**: up to three web searches built from the lead's own record (company name,
   website domain, industry, place, the chosen offer), each a ``web_search`` tool call
   under the same tool policy an agent gets (governance rules can block it or ask for an
   approval, and every call is audited by the runtime), and only while the workspace's
   daily research caps allow.
2. **Profile**: each new result becomes an evidence record (URL, title, excerpt, the date
   left unknown unless a person enters it, a confidence from whether it is the company's own
   site). Result text is untrusted data: stored and shown as data, never followed.
3. **Score**: proposed inferences that cite the evidence they rest on (the model through the
   budgeted gateway, or a template). Research never sets a score itself, and never suggests
   urgency or the buyer's authority: only a conversation confirms those.
4. **Draft**: a fit explanation for review listing what is still unknown.

Nothing is sent or contacted. The lead moves from ``discovered`` to ``researched``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from ..agent_tools import ToolCall, ToolPolicyOutcome, ToolRegistry
from ..schemas import TenantContext, new_id, utc_now
from ..web_search import WEB_SEARCH_TOOL
from . import assist
from .models import (
    Account,
    Confidence,
    Evidence,
    Inference,
    Lead,
    LeadState,
    Product,
    RecordSource,
    ResearchRun,
    ResearchStatus,
    ResearchStep,
)
from .service import audit, clean_url, domain_of, record_activity, stamp_state, url_key
from .store import Query, open_business_store

MAX_QUERIES = 3
MAX_EVIDENCE_PER_RUN = 10

_registry: ToolRegistry | None = None


def configure(registry: ToolRegistry) -> None:
    global _registry
    _registry = registry


def web_search_available() -> bool:
    return _registry is not None and WEB_SEARCH_TOOL in _registry.names


class ResearchRefused(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def queries_for(account: Account, product: Product | None) -> list[str]:
    """Searches from the lead's own record only, never from page text."""
    name = " ".join(account.name.split())[:120]
    place = " ".join(part for part in (account.city, account.country) if part)
    found = [f'"{name}" {account.domain}' if account.domain else f'"{name}" {place}'.strip()]
    if account.industry or place:
        found.append(f'"{name}" {account.industry} {place}'.strip())
    if product is not None:
        found.append(f'"{name}" {product.name}'[:300])
    unique: list[str] = []
    for query in found:
        query = " ".join(query.split())[:300]
        if query and query not in unique:
            unique.append(query)
    return unique[:MAX_QUERIES]


def confidence_for(account: Account, url: str, title: str, excerpt: str) -> Confidence:
    domain = domain_of(url)
    if account.domain and domain and (domain == account.domain or domain.endswith("." + account.domain)):
        return Confidence.HIGH
    if account.name.casefold() in f"{title} {excerpt}".casefold():
        return Confidence.MEDIUM
    return Confidence.LOW


async def _search(context: TenantContext, query: str) -> tuple[str, list[dict[str, str]], str]:
    """One mediated web search: ("done" | "blocked" | "failed", results, summary)."""
    from ..dependencies import _unit_of_work
    from ..model_gateway import MockModelGateway
    from ..runtime import AgentRuntime

    registry = _registry
    if registry is None:  # pragma: no cover - checked before a run starts
        return "blocked", [], "Web search is not set up."
    call = ToolCall(name=WEB_SEARCH_TOOL, arguments={"query": query})

    def allowed() -> tuple[bool, str]:
        with _unit_of_work(context, require_membership=False) as repository:
            runtime = AgentRuntime(MockModelGateway(), repository, tools=registry)
            decision = runtime.evaluate(call, context)
            if decision.outcome != ToolPolicyOutcome.ALLOW:
                runtime._audit(context, "tool.call", call.name, "blocked", call.name,
                               {"tool": call.name, "surface": "business_research", "reason": decision.reason[:200]})
                return False, decision.reason
            return True, ""

    ok, reason = await asyncio.to_thread(allowed)
    if not ok:
        return "blocked", [], f"Web search was not allowed by policy: {reason}"[:500]
    result = await registry.execute(call, context)

    def record() -> None:
        with _unit_of_work(context, require_membership=False) as repository:
            AgentRuntime(MockModelGateway(), repository, tools=registry)._audit(
                context, "tool.call", call.name, result.status, call.name,
                {**result.audit_metadata, "tool": call.name, "surface": "business_research"},
            )

    await asyncio.to_thread(record)
    if result.status != "succeeded":
        return ("blocked" if result.status == "blocked" else "failed"), [], result.summary[:500]
    raw = result.output.get("results") if isinstance(result.output, dict) else None
    items = [item for item in (raw or []) if isinstance(item, dict)]
    return "done", items, f"{len(items)} result(s)."


def _load(context: TenantContext, lead_id: str, now: datetime) -> tuple[Lead, Account, Product | None, set[str], int]:
    with open_business_store(context) as store:
        lead = store.get("leads", lead_id)
        if lead is None:
            raise ResearchRefused(404, "Lead not found")
        if lead.state in {LeadState.SUPPRESSED, LeadState.WON, LeadState.LOST}:
            raise ResearchRefused(409, f"This lead is {lead.state.value}; research only open leads.")
        account = store.get("accounts", lead.account_id)
        if account is None:  # pragma: no cover - foreign key
            raise ResearchRefused(404, "The lead's account is missing")
        product = store.get("products", lead.product_id) if lead.product_id else None
        settings = store.get_settings()
        from .pipeline import load_settings, usage  # lead research and pipeline runs share the daily caps

        today = usage(store, now)
        runs, searches = today["runs"], today["searches"]
        outreach = load_settings(store, context)
        if outreach.paused:
            raise ResearchRefused(409, f"Business research and outreach are paused: {outreach.paused_reason or 'an owner paused them'}.")
        if runs >= settings.research_runs_per_day:
            raise ResearchRefused(429, f"Today's research cap is reached ({settings.research_runs_per_day} runs a day). An owner can change it in Business settings.")
        remaining = settings.research_searches_per_day - searches
        if remaining <= 0:
            raise ResearchRefused(429, f"Today's web search cap is reached ({settings.research_searches_per_day} searches a day). An owner can change it in Business settings.")
        existing, _ = store.find("evidence", Query(where={"lead_id": lead.id}, limit=500))
    return lead, account, product, {item.url_key for item in existing}, remaining


async def run_research(context: TenantContext, lead_id: str) -> ResearchRun:
    now = utc_now()
    lead, account, product, known_urls, remaining = await asyncio.to_thread(_load, context, lead_id, now)
    if not web_search_available():
        raise ResearchRefused(503, "Web search is not set up on this server (ANUM_SEARXNG_URL), or it is switched off under Integrations. Add sources by hand instead.")
    steps: list[ResearchStep] = []
    queries = queries_for(account, product)[: max(0, remaining)]
    results: list[dict[str, str]] = []
    statuses: list[str] = []
    for query in queries:
        status, found, summary = await _search(context, query)
        statuses.append(status)
        results.extend(found)
        if status == "blocked":
            break
    blocked = statuses and statuses[-1] == "blocked"
    steps.append(ResearchStep(step="find", status="blocked" if blocked else ("failed" if statuses and all(s == "failed" for s in statuses) else "done"),
                              summary=f"{len(statuses)} search(es), {len(results)} result(s)." if not blocked else summary))

    run_id = new_id("bres")
    evidence: list[Evidence] = []
    for item in results:
        url = clean_url(str(item.get("url", "")))
        if url is None:
            continue
        key = url_key(url)
        if key in known_urls:
            continue
        known_urls.add(key)
        title = " ".join(str(item.get("title", "")).split())[:300]
        excerpt = " ".join(str(item.get("snippet", "")).split())[:1200]
        evidence.append(Evidence(
            id=new_id("bevi"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, lead_id=lead.id, account_id=account.id,
            url=url, url_key=key, title=title, published_on=None, excerpt=excerpt, confidence=confidence_for(account, url, title, excerpt),
            source=RecordSource.RESEARCH, research_run_id=run_id, created_by=context.user_id, created_at=now, updated_at=now,
        ))
        if len(evidence) >= MAX_EVIDENCE_PER_RUN:
            break
    steps.append(ResearchStep(step="profile", status="done" if results else "skipped", summary=f"{len(evidence)} new source(s) saved as evidence."))

    proposed, generator, note = await assist.research_inferences(context, account, lead, product, evidence)
    inferences = [
        Inference(
            id=new_id("binf"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, lead_id=lead.id, statement=item.statement,
            criterion=item.criterion, confidence=item.confidence, basis_evidence_ids=[evidence[index].id for index in item.evidence],
            generator=generator, created_by="agent:business_research", created_at=now, updated_at=now,
        )
        for item in proposed
    ]
    steps.append(ResearchStep(step="score", status="done" if inferences else "skipped",
                              summary=(f"{len(inferences)} inference(s) proposed for review." + (f" {note}" if note else ""))[:500]))
    fit = assist.fit_summary(account, lead, product, len(known_urls))
    inferences.append(Inference(
        id=new_id("binf"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, lead_id=lead.id, statement=fit[:1000],
        criterion=None, confidence=Confidence.LOW, basis_evidence_ids=[item.id for item in evidence[:5]], generator="template",
        created_by="agent:business_research", created_at=now, updated_at=now,
    ))
    steps.append(ResearchStep(step="draft", status="done", summary="Fit explanation drafted for review."))

    if blocked:
        status = ResearchStatus.BLOCKED
    elif statuses and all(item == "failed" for item in statuses):
        status = ResearchStatus.FAILED
    elif any(item != "done" for item in statuses):
        status = ResearchStatus.PARTIAL
    else:
        status = ResearchStatus.SUCCEEDED
    run = ResearchRun(
        id=run_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, lead_id=lead.id, status=status,
        queries=len(statuses), results=len(results), evidence_added=len(evidence), inferences_added=len(inferences), steps=steps,
        error=next((step.summary for step in steps if step.status in {"blocked", "failed"}), ""), created_by=context.user_id,
        created_at=now, updated_at=utc_now(),
    )

    def save() -> ResearchRun:
        with open_business_store(context) as store:
            current = store.get("leads", lead.id, for_update=True)
            if current is None:
                raise ResearchRefused(404, "The lead was deleted during research")
            store.add("research_runs", run)
            for item in evidence:
                store.add("evidence", item)
            for item in inferences:
                store.add("inferences", item)
            if current.state == LeadState.DISCOVERED and status != ResearchStatus.FAILED:
                stamp_state(current, LeadState.RESEARCHED, now)
            if evidence:
                current.last_verified_at = now
            current.updated_at = utc_now()
            store.save("leads", current)
            record_activity(store, context, "research", f"Research: {run.queries} search(es), {run.evidence_added} new source(s), {run.inferences_added} inference(s) to review.",
                            lead_id=lead.id, account_id=account.id, metadata={"run_id": run.id, "status": status.value})
        return run

    saved = await asyncio.to_thread(save)
    metadata: dict[str, Any] = {"lead_id": lead.id, "queries": run.queries, "results": run.results, "evidence": run.evidence_added, "status": status.value}
    await asyncio.to_thread(audit, None, context, "business.lead.researched", run.id, metadata=metadata)
    return saved
