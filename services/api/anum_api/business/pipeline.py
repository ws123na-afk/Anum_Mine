"""The prospecting pipeline: discover, then profile (docs/business.md#prospecting-pipeline).

Harvey's cost-first loop (docs/business/HARVEY_PARITY.md), written as ANUM code:

1. **Estimate**: a run is planned and estimated before anything is called: searches, page
   reads and model calls at most, the estimated model cost, today's caps and what is left of
   the workspace's monthly model budget. Nothing runs until a person confirms the estimate.
2. **Confirm, then run within the estimate**: the run never makes more searches, page reads or
   model calls than were confirmed, re-checks the daily caps before each call, stops when the
   monthly model budget is used up (the budgeted gateway refuses the call), and reads the
   pause (kill switch) and the stop button between steps.
3. **Discover** (``kind=discover``): ``web_search`` calls built from the person's market terms
   and places (never from page text). Results are filtered for junk (directories, social
   networks, media, institutions), deduplicated by domain and by name, and new companies become
   an account, a ``discovered`` lead and an evidence row (the result URL, title and excerpt).
4. **Profile** (``kind=profile``): for each lead, the company's own https pages are read with
   ``web.fetch_page`` (robots.txt obeyed, SSRF guard, rate limits). Only the signals the owner
   confirmed are collected, each as an observation with the page URL, the excerpt and the day.
   A page that could not be read records nothing: unknown stays unknown. Then the research skill
   proposes inferences that cite those pages (model through the budgeted gateway, or template).

Every tool call goes through the runtime's ``ToolPolicy`` and is audited, as the lead research
skill's searches are. Nothing is sent or contacted. Runs work in short units of work: no
transaction is ever open across a tool or model call (CLAUDE.md, event loop rules).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

from ..agent_tools import ToolCall, ToolPolicyOutcome, ToolResult
from ..model_budget import ModelBudgetExceededError
from ..schemas import TenantContext, new_id, utc_now
from ..web_search import WEB_SEARCH_TOOL
from . import assist, research, signals
from .fetch import FETCH_TOOL
from .models import (
    OPEN_LEAD_STATES,
    Account,
    Confidence,
    Evidence,
    Inference,
    Lead,
    LeadState,
    RecordSource,
)
from .outreach_models import Estimate, Observation, OutreachSettings, PipelineRun, RunKind, RunStatus, RunStep, SignalDecision, SignalStatus
from .service import audit, clean_url, domain_of, name_key, record_activity, stamp_state, url_key, usage_today
from .store import BusinessStore, Query, open_business_store

logger = logging.getLogger("anum.business")

MAX_QUERIES = 10
MAX_TERMS = 5
MAX_PLACES = 5
MAX_NEW_LEADS = 25
MAX_PROFILE_LEADS = 20
# What one profile model call is assumed to use, for the estimate (the gateway meters the real figure).
EST_INPUT_TOKENS = 3000
EST_OUTPUT_TOKENS = 500
STALE_AFTER = timedelta(minutes=15)


class PipelineRefused(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# -- settings and usage ------------------------------------------------------------------------------


def load_settings(store: BusinessStore, context: TenantContext) -> OutreachSettings:
    found = store.first("outreach_settings")
    if found is not None:
        return found
    now = utc_now()
    return OutreachSettings(
        id=new_id("bost"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, updated_at=now,
        signals={spec.code: SignalDecision() for spec in signals.CATALOG},
    )


def save_settings(store: BusinessStore, value: OutreachSettings) -> OutreachSettings:
    value.updated_at = utc_now()
    if store.get("outreach_settings", value.id) is None:
        return store.add("outreach_settings", value)
    return store.save("outreach_settings", value)


def usage(store: BusinessStore, now: datetime) -> dict[str, int]:
    """Today's (UTC) searches, page reads and runs in this workspace: lead research and pipeline runs together."""
    research_runs, research_searches = usage_today(store, now)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    runs, _ = store.find("pipeline_runs", Query(where={"status": {s for s in RunStatus if s != RunStatus.ESTIMATED}}, created_from=start, limit=1000, order_by="created_at"))
    return {
        "runs": research_runs + len(runs),
        "searches": research_searches + sum(run.used.searches for run in runs),
        "page_fetches": sum(run.used.page_fetches for run in runs),
        "model_calls": sum(run.used.model_calls for run in runs),
    }


def value_proposition(store: BusinessStore, product_id: str | None, language: str | None = None) -> str:
    """The product's pitch from its target customer profile: one language, or both (for draft checks)."""
    profile = store.first("target_profiles", product_id=product_id) if product_id else None
    if profile is None:
        return ""
    if language == "ar":
        return profile.value_proposition_ar or profile.value_proposition
    if language == "en":
        return profile.value_proposition
    return f"{profile.value_proposition} {profile.value_proposition_ar}".strip()


def product_fits(store: BusinessStore, lead: Lead) -> list[signals.Fit]:
    """The lead matched against every live product's target profile, best fit first (deterministic)."""
    account = store.get("accounts", lead.account_id)
    if account is None:
        return []
    evidence, _ = store.find("evidence", Query(where={"lead_id": lead.id}, limit=100, order_by="created_at"))
    observations, _ = store.find("observations", Query(where={"lead_id": lead.id}, limit=300, order_by="created_at"))
    texts = [f"{item.title} {item.excerpt}" for item in evidence] + [f"{item.value_text} {item.excerpt}" for item in observations]
    products, _ = store.find("products", Query(limit=100, order_by="name", descending=False))
    fits: list[signals.Fit] = []
    for product in products:
        if product.maturity.value == "retired":
            continue
        profile = store.first("target_profiles", product_id=product.id)
        fits.append(signals.product_fit(profile, product.id, product.name, account, texts))
    fits.sort(key=lambda fit: (fit.score is None, fit.disqualified, -(fit.score or 0), fit.product_name.casefold()))
    return fits


# -- mediated tool calls ----------------------------------------------------------------------------


async def call_tool(context: TenantContext, call: ToolCall, surface: str) -> tuple[str, ToolResult | None, str]:
    """One tool call under the runtime's policy, audited: ("done" | "blocked" | "failed", result, summary)."""
    from ..dependencies import _unit_of_work
    from ..model_gateway import MockModelGateway
    from ..runtime import AgentRuntime

    registry = research._registry
    if registry is None or call.name not in registry.names:
        return "blocked", None, f"The {call.name} tool is not set up on this server."

    def allowed() -> tuple[bool, str]:
        with _unit_of_work(context, require_membership=False) as repository:
            runtime = AgentRuntime(MockModelGateway(), repository, tools=registry)
            decision = runtime.evaluate(call, context)
            if decision.outcome != ToolPolicyOutcome.ALLOW:
                runtime._audit(context, "tool.call", call.name, "blocked", call.name,
                               {"tool": call.name, "surface": surface, "reason": decision.reason[:200]})
                return False, decision.reason
            return True, ""

    ok, reason = await asyncio.to_thread(allowed)
    if not ok:
        return "blocked", None, f"{call.name} was not allowed by policy: {reason}"[:500]
    result = await registry.execute(call, context)

    def record() -> None:
        with _unit_of_work(context, require_membership=False) as repository:
            AgentRuntime(MockModelGateway(), repository, tools=registry)._audit(
                context, "tool.call", call.name, result.status, call.name, {**result.audit_metadata, "tool": call.name, "surface": surface},
            )

    await asyncio.to_thread(record)
    if result.status != "succeeded":
        return ("blocked" if result.status == "blocked" else "failed"), result, result.summary[:500]
    return "done", result, result.summary[:500]


def tools_available() -> dict[str, bool]:
    registry = research._registry
    names = registry.names if registry is not None else set()
    return {"web_search": WEB_SEARCH_TOOL in names, "page_fetch": FETCH_TOOL in names}


# -- planning and estimates ------------------------------------------------------------------------


def _clean(values: list[str], limit: int, size: int = 80) -> list[str]:
    found: list[str] = []
    for value in values:
        text = " ".join(str(value).split())[:size]
        if text and text.casefold() not in {item.casefold() for item in found}:
            found.append(text)
    return found[:limit]


def discovery_queries(terms: list[str], places: list[str], max_queries: int) -> list[str]:
    """Market term x place, from what the person typed only."""
    queries = [f"{term} {place}".strip() for term in terms for place in (places or [""])]
    return _clean(queries, min(max_queries, MAX_QUERIES), 200)


def _model_cost(context: TenantContext, calls: int) -> tuple[float | None, int, bool]:
    """(estimated cost in USD or None when unpriced, tokens, model connected)."""
    from ..model_gateway import ModelRoute, estimate_cost_usd, route_gateway

    gateway = route_gateway(assist.workspace_gateway(context), ModelRoute.FAST)
    provider = str(getattr(gateway, "provider", "mock") or "mock")
    if provider == "mock" or calls == 0:
        return 0.0, 0, provider != "mock"
    tokens = calls * (EST_INPUT_TOKENS + EST_OUTPUT_TOKENS)
    per_call = estimate_cost_usd(provider, str(getattr(gateway, "model", "") or ""), EST_INPUT_TOKENS, EST_OUTPUT_TOKENS)
    return (None if per_call is None else round(per_call * calls, 6)), tokens, True


def _budget_left(context: TenantContext) -> tuple[float | None, int | None]:
    from ..model_budget import _overview

    overview = _overview(context, utc_now())
    cost: float | None = None
    tokens: int | None = None
    for view in (overview.tenant, overview.workspace):
        if view.budget is None:
            continue
        if view.budget.monthly_cost_limit_usd is not None:
            left = max(0.0, view.budget.monthly_cost_limit_usd - view.usage.estimated_cost_usd)
            cost = left if cost is None else min(cost, left)
        if view.budget.monthly_token_limit is not None:
            left_tokens = max(0, view.budget.monthly_token_limit - view.usage.total_tokens)
            tokens = left_tokens if tokens is None else min(tokens, left_tokens)
    return cost, tokens


def estimate(context: TenantContext, store: BusinessStore, kind: RunKind, plan: dict[str, Any]) -> Estimate:
    """What the run will use at most, and whether that fits today's caps and the model budget."""
    settings = store.get_settings()
    outreach = load_settings(store, context)
    used = usage(store, utc_now())
    est = Estimate()
    est.searches_left_today = max(0, settings.research_searches_per_day - used["searches"])
    est.fetches_left_today = max(0, outreach.fetches_per_day - used["page_fetches"])
    confirmed = outreach.confirmed()
    available = tools_available()
    if kind == RunKind.DISCOVER:
        est.searches = len(plan.get("queries", []))
        if not available["web_search"]:
            est.problems.append("Web search is not set up on this server (ANUM_SEARXNG_URL), or it is switched off under Integrations.")
        if "FOUND_IN_SEARCH" not in confirmed:
            est.notes.append("The signal \"Found in a market search\" is not confirmed, so the search query is not recorded with each lead.")
    else:
        leads = plan.get("lead_ids", [])
        est.page_fetches = len(leads) * signals.pages_needed(confirmed, outreach.pages_per_lead)
        est.model_calls = len(leads)
        if not available["page_fetch"]:
            est.problems.append("The page reader is not set up on this server.")
        if not confirmed - {"FOUND_IN_SEARCH"}:
            est.problems.append("No profile signal is confirmed yet. Confirm the signals you want collected first: nothing is collected until you choose.")
    cost, tokens, connected = _model_cost(context, est.model_calls)
    if not connected:
        est.model_calls = 0
        if kind == RunKind.PROFILE:
            est.notes.append("No model is connected, so inferences come from a template and the run costs no model budget.")
    est.estimated_cost_usd, est.estimated_tokens = cost, tokens
    if cost is None:
        est.notes.append("The model has no configured price, so its cost cannot be estimated; tokens are still metered.")
    est.budget_cost_left_usd, est.budget_tokens_left = _budget_left(context)
    if est.searches > est.searches_left_today:
        est.problems.append(f"The run needs {est.searches} search(es) but {est.searches_left_today} are left today. An owner can raise the cap in Business settings.")
    if est.page_fetches > est.fetches_left_today:
        est.problems.append(f"The run needs up to {est.page_fetches} page read(s) but {est.fetches_left_today} are left today. An owner can raise the cap.")
    if used["runs"] >= settings.research_runs_per_day:
        est.problems.append(f"Today's research cap is reached ({settings.research_runs_per_day} runs a day).")
    if est.model_calls and est.budget_tokens_left is not None and est.estimated_tokens > est.budget_tokens_left:
        est.problems.append(f"The model budget has {est.budget_tokens_left} tokens left this month; the run may need {est.estimated_tokens}.")
    if est.model_calls and est.budget_cost_left_usd is not None and (cost or 0) > est.budget_cost_left_usd:
        est.problems.append(f"The model budget has USD {est.budget_cost_left_usd:.4f} left this month; the run is estimated at USD {cost:.4f}.")
    if outreach.paused:
        est.problems.append(f"Business outreach and research are paused: {outreach.paused_reason or 'an owner paused them'}.")
    est.fits = not est.problems
    return est


def plan_run(context: TenantContext, kind: RunKind, *, product_id: str | None, terms: list[str], places: list[str], max_queries: int,
             max_new_leads: int, lead_ids: list[str]) -> PipelineRun:
    """Create an estimated run (nothing is called yet)."""
    now = utc_now()
    with open_business_store(context) as store:
        plan: dict[str, Any]
        if kind == RunKind.DISCOVER:
            # Every discovery run is for one product, and reads that product's target customer profile.
            if not product_id:
                raise PipelineRefused(422, "Choose the product this discovery run is for.")
            if store.get("products", product_id) is None:
                raise PipelineRefused(404, "Product not found")
            profile = store.first("target_profiles", product_id=product_id)
            clean_terms = _clean(terms, MAX_TERMS) or (_clean([*profile.industries, *profile.keywords], MAX_TERMS) if profile else [])
            clean_places = _clean(places, MAX_PLACES) or (_clean(profile.regions, 2) if profile else [])
            if not clean_terms:
                raise PipelineRefused(422, "Give at least one market term (for example \"concrete supplier\"), or fill in the product's target customer profile.")
            plan = {"terms": clean_terms, "places": clean_places, "queries": discovery_queries(clean_terms, clean_places, max_queries),
                    "max_new_leads": max(1, min(max_new_leads, MAX_NEW_LEADS))}
        else:
            wanted = list(dict.fromkeys(lead_ids))[:MAX_PROFILE_LEADS]
            if not wanted:
                raise PipelineRefused(422, "Choose at least one lead to profile.")
            usable: list[str] = []
            skipped: list[str] = []
            for lead_id in wanted:
                lead = store.get("leads", lead_id)
                if lead is None:
                    raise PipelineRefused(404, "Lead not found")
                account = store.get("accounts", lead.account_id)
                if lead.state not in OPEN_LEAD_STATES or account is None or not account.domain:
                    skipped.append(lead_id)
                else:
                    usable.append(lead_id)
            if not usable:
                raise PipelineRefused(409, "None of the chosen leads can be profiled: each needs an open lead and a company website.")
            plan = {"lead_ids": usable, "skipped": skipped}
        run = PipelineRun(
            id=new_id("bpip"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, kind=kind, product_id=product_id,
            plan=plan, created_by=context.user_id, created_at=now, updated_at=now,
        )
        run.estimate = estimate(context, store, kind, plan)
        store.add("pipeline_runs", run)
    audit(None, context, "business.pipeline.estimated", run.id, metadata={"kind": kind.value, "searches": run.estimate.searches,
                                                                           "page_fetches": run.estimate.page_fetches, "fits": run.estimate.fits})
    return run


def view_status(run: PipelineRun, now: datetime | None = None) -> str:
    """A running run whose heartbeat stopped (an API restart) shows as interrupted and can be resumed."""
    if run.status == RunStatus.RUNNING and run.heartbeat_at is not None and (now or utc_now()) - run.heartbeat_at > STALE_AFTER:
        return "interrupted"
    return run.status.value


def confirm(context: TenantContext, run_id: str) -> PipelineRun:
    """Re-estimate and mark the run running; the caller schedules :func:`execute`."""
    now = utc_now()
    with open_business_store(context) as store:
        run = store.get("pipeline_runs", run_id, for_update=True)
        if run is None:
            raise PipelineRefused(404, "Run not found")
        resumable = run.status == RunStatus.RUNNING and view_status(run, now) == "interrupted"
        if run.status != RunStatus.ESTIMATED and not resumable:
            raise PipelineRefused(409, f"This run is {view_status(run, now)}; estimate a new run instead.")
        if not resumable:
            fresh = estimate(context, store, run.kind, run.plan)
            run.estimate = fresh
            if not fresh.fits:
                store.save("pipeline_runs", run)
                raise PipelineRefused(409, "The run no longer fits: " + " ".join(fresh.problems))
        run.status = RunStatus.RUNNING
        run.confirmed_by = run.confirmed_by or context.user_id
        run.confirmed_at = run.confirmed_at or now
        run.heartbeat_at = now
        run.stop_requested = False
        run.updated_at = now
        store.save("pipeline_runs", run)
    audit(None, context, "business.pipeline.confirmed", run.id, metadata={"kind": run.kind.value, "resumed": resumable})
    return run


def request_stop(context: TenantContext, run_id: str) -> PipelineRun:
    with open_business_store(context) as store:
        run = store.get("pipeline_runs", run_id, for_update=True)
        if run is None:
            raise PipelineRefused(404, "Run not found")
        if run.status == RunStatus.ESTIMATED:
            run.status, run.stop_reason, run.finished_at = RunStatus.STOPPED, "Cancelled before it started.", utc_now()
        elif run.status == RunStatus.RUNNING:
            run.stop_requested = True
        else:
            raise PipelineRefused(409, f"This run is already {run.status.value}.")
        run.updated_at = utc_now()
        store.save("pipeline_runs", run)
    audit(None, context, "business.pipeline.stop", run.id, metadata={"kind": run.kind.value})
    return run


# -- execution ----------------------------------------------------------------------------------


class _Runner:
    def __init__(self, context: TenantContext, run_id: str) -> None:
        self.context = context
        self.run_id = run_id
        self.run: PipelineRun | None = None
        self.settings: OutreachSettings | None = None
        self.confirmed: set[str] = set()

    def _step(self, step: str, status: str, summary: str, target: str | None = None) -> None:
        if self.run is None:
            raise RuntimeError("the run is not loaded")
        self.run.steps.append(RunStep(at=utc_now(), step=step, status=status, summary=summary[:500], target=(target or None) and target[:200]))
        self.run.steps = self.run.steps[-200:]

    def _sync(self) -> bool:
        """Save progress and read the stop button and the pause. False: stop now."""
        if self.run is None:
            raise RuntimeError("the run is not loaded")
        with open_business_store(self.context) as store:
            current = store.get("pipeline_runs", self.run_id, for_update=True)
            if current is None:
                return False
            settings = load_settings(store, self.context)
            self.run.stop_requested = current.stop_requested
            self.run.heartbeat_at = utc_now()
            self.run.updated_at = utc_now()
            store.save("pipeline_runs", self.run)
        if self.run.stop_requested:
            self.run.stop_reason = self.run.stop_reason or "Stopped by a person."
            return False
        if settings.paused:
            self.run.stop_reason = f"Paused: {settings.paused_reason or 'an owner paused Business outreach and research'}."
            return False
        return True

    def _caps_left(self, field: str) -> int:
        if self.run is None:
            raise RuntimeError("the run is not loaded")
        with open_business_store(self.context) as store:
            used = usage(store, utc_now())
            settings = store.get_settings()
            outreach = load_settings(store, self.context)
        if field == "searches":
            daily = settings.research_searches_per_day - used["searches"]
            confirmed = self.run.estimate.searches - self.run.used.searches
        else:
            daily = outreach.fetches_per_day - used["page_fetches"]
            confirmed = self.run.estimate.page_fetches - self.run.used.page_fetches
        return min(daily, confirmed)

    async def load(self) -> bool:
        def read() -> tuple[PipelineRun | None, OutreachSettings]:
            with open_business_store(self.context) as store:
                return store.get("pipeline_runs", self.run_id), load_settings(store, self.context)

        self.run, self.settings = await asyncio.to_thread(read)
        if self.run is None or self.run.status != RunStatus.RUNNING:
            return False
        self.confirmed = self.settings.confirmed()
        return True

    async def finish(self, error: str | None = None) -> None:
        if self.run is None:
            raise RuntimeError("the run is not loaded")
        run = self.run
        if error:
            run.status = RunStatus.FAILED
            run.stop_reason = error[:300]
        elif run.stop_reason:
            run.status = RunStatus.STOPPED
        elif any(step.status in {"failed", "blocked"} for step in run.steps):
            run.status = RunStatus.PARTIAL
        else:
            run.status = RunStatus.SUCCEEDED
        run.finished_at = run.updated_at = utc_now()

        def save() -> None:
            with open_business_store(self.context) as store:
                if store.get("pipeline_runs", run.id, for_update=True) is not None:
                    store.save("pipeline_runs", run)

        await asyncio.to_thread(save)
        await asyncio.to_thread(audit, None, self.context, "business.pipeline.finished", run.id, metadata={
            "kind": run.kind.value, "status": run.status.value, "searches": run.used.searches, "page_fetches": run.used.page_fetches,
            "model_calls": run.used.model_calls,
        })

    # -- discover --------------------------------------------------------------------------------

    async def discover(self) -> None:
        if self.run is None:
            raise RuntimeError("the run is not loaded")
        run = self.run
        queries: list[str] = run.plan.get("queries", [])
        done_queries: list[str] = run.results.setdefault("queries_done", [])
        created: list[str] = run.results.setdefault("lead_ids", [])
        run.results.setdefault("known", 0)
        run.results.setdefault("junk", 0)
        run.results.setdefault("possible_duplicates", [])
        max_new = int(run.plan.get("max_new_leads", MAX_NEW_LEADS))
        for query in queries:
            if query in done_queries:
                continue  # resumed run: this query already ran
            if not await asyncio.to_thread(self._sync):
                return
            if len(created) >= max_new:
                self._step("discover", "skipped", f"Stopped at {max_new} new lead(s), the run's limit.")
                break
            if await asyncio.to_thread(self._caps_left, "searches") <= 0:
                run.stop_reason = "The daily search cap or the confirmed number of searches is used up."
                return
            status, result, summary = await call_tool(self.context, ToolCall(name=WEB_SEARCH_TOOL, arguments={"query": query}), "business_discover")
            if result is not None or status == "done":
                run.used.searches += 1
            if status != "done" or result is None:
                self._step("search", status, summary, query)
                done_queries.append(query)
                if status == "blocked":
                    return
                continue
            items = [item for item in (result.output.get("results") or []) if isinstance(item, dict)]
            added, known, junk, dupes = await asyncio.to_thread(self._save_discovered, query, items, max_new - len(created))
            created.extend(added)
            run.results["known"] += known
            run.results["junk"] += junk
            run.results["possible_duplicates"] = (run.results["possible_duplicates"] + dupes)[-50:]
            done_queries.append(query)
            self._step("search", "done", f"{len(items)} result(s): {len(added)} new lead(s), {known} already known, {junk} filtered as directories or media"
                       + (f", {len(dupes)} possible duplicate(s) left for you" if dupes else "") + ".", query)

    def _save_discovered(self, query: str, items: list[dict[str, Any]], room: int) -> tuple[list[str], int, int, list[dict[str, str]]]:
        if self.run is None:
            raise RuntimeError("the run is not loaded")
        context, now = self.context, utc_now()
        added: list[str] = []
        known = junk = 0
        dupes: list[dict[str, str]] = []
        seen: set[str] = set()
        with open_business_store(context) as store:
            product = store.get("products", self.run.product_id) if self.run.product_id else None
            profile = store.first("target_profiles", product_id=product.id) if product else None
            for item in items:
                url = clean_url(str(item.get("url", "")))
                domain = domain_of(url) if url else None
                title = " ".join(str(item.get("title", "")).split())[:300]
                if url is None or domain is None:
                    junk += 1
                    continue
                name = signals.company_name(title, domain)
                if signals.is_junk(domain, title) or signals.disqualified_by(profile, f"{title} {item.get('snippet', '')}"):
                    junk += 1  # a directory, social network or media site, or a product disqualifier
                    continue
                if domain in seen:
                    continue
                seen.add(domain)
                if store.first("accounts", domain=domain) is not None:
                    known += 1
                    continue
                key = name_key(name)
                same_name = store.first("accounts", name_key=key) if key else None
                if same_name is not None:
                    dupes.append({"name": name[:120], "domain": domain, "matches_account_id": same_name.id})
                    continue
                if len(added) >= room:
                    break
                account = Account(
                    id=new_id("bacc"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, name=name, name_key=key,
                    website=f"https://{domain}", domain=domain, industry=(self.run.plan.get("terms") or [""])[0][:120],
                    city=(self.run.plan.get("places") or [""])[0][:80], source=RecordSource.RESEARCH, owner_id=context.user_id,
                    created_by=context.user_id, created_at=now, updated_at=now,
                )
                store.add("accounts", account)
                lead = Lead(
                    id=new_id("blead"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, account_id=account.id,
                    product_id=product.id if product else None, title=(f"{name}: {product.name}" if product else name)[:200],
                    owner_id=context.user_id, source=RecordSource.RESEARCH, created_by=context.user_id, created_at=now, updated_at=now,
                )
                store.add("leads", lead)
                excerpt = " ".join(str(item.get("snippet", "")).split())[:1200]
                evidence = Evidence(
                    id=new_id("bevi"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, lead_id=lead.id, account_id=account.id,
                    url=url, url_key=url_key(url), title=title, excerpt=excerpt, confidence=research.confidence_for(account, url, title, excerpt),
                    source=RecordSource.RESEARCH, research_run_id=self.run.id, created_by=context.user_id, created_at=now, updated_at=now,
                )
                store.add("evidence", evidence)
                if "FOUND_IN_SEARCH" in self.confirmed:
                    store.add("observations", Observation(
                        id=new_id("bobs"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, lead_id=lead.id, account_id=account.id,
                        run_id=self.run.id, evidence_id=evidence.id, signal_code="FOUND_IN_SEARCH", value_text=query[:300], url=url,
                        excerpt=(title + ": " + excerpt)[:600], observed_on=now.date(), created_by=context.user_id, created_at=now, updated_at=now,
                    ))
                record_activity(store, context, "discovered", f"Found by a market search: {query}"[:500], lead_id=lead.id, account_id=account.id,
                                metadata={"run_id": self.run.id})
                added.append(lead.id)
        return added, known, junk, dupes

    # -- profile --------------------------------------------------------------------------------

    async def _fetch(self, url: str, lead_id: str) -> tuple[str, dict[str, Any] | None, str]:
        if self.run is None:
            raise RuntimeError("the run is not loaded")
        if await asyncio.to_thread(self._caps_left, "page_fetches") <= 0:
            return "capped", None, "The daily page-read cap or the confirmed number of page reads is used up."
        status, result, summary = await call_tool(self.context, ToolCall(name=FETCH_TOOL, arguments={"url": url}), "business_profile")
        if result is not None:
            self.run.used.page_fetches += 1
        if status != "done" or result is None:
            return status, None, summary
        return "done", result.output, summary

    async def profile(self) -> None:
        if self.run is None:
            raise RuntimeError("the run is not loaded")
        run = self.run
        done: list[str] = run.results.setdefault("profiled", [])
        run.results.setdefault("observations", 0)
        run.results.setdefault("inferences", 0)
        for lead_id in run.plan.get("lead_ids", []):
            if lead_id in done:
                continue
            if not await asyncio.to_thread(self._sync):
                return
            stop = await self._profile_one(lead_id)
            done.append(lead_id)
            if stop:
                return

    def _load_lead(self, lead_id: str) -> tuple[Lead | None, Account | None, Any]:
        with open_business_store(self.context) as store:
            lead = store.get("leads", lead_id)
            account = store.get("accounts", lead.account_id) if lead else None
            product = store.get("products", lead.product_id) if lead and lead.product_id else None
        return lead, account, product

    async def _profile_one(self, lead_id: str) -> bool:
        """Profile one lead; True when the run must stop (cap or budget)."""
        if self.run is None:
            raise RuntimeError("the run is not loaded")
        lead, account, product = await asyncio.to_thread(self._load_lead, lead_id)
        if lead is None or account is None or not account.domain or lead.state not in OPEN_LEAD_STATES:
            self._step("profile", "skipped", "The lead is closed, gone or has no website.", lead_id)
            return False
        domain = account.domain
        base = f"https://{domain}"
        pages: list[tuple[str, str, str]] = []  # (url, title, html)
        found: list[tuple[signals.Found, str]] = []  # (detection, page url)
        status, home, summary = await self._fetch(base + "/", lead.id)
        if status == "capped":
            self.run.stop_reason = summary
            return True
        if home is None:
            self._step("profile", status, f"{domain}: {summary} Nothing was recorded, so its signals stay unknown.", account.name)
            return False
        home_url = str(home.get("final_url") or base + "/")
        home_html = str(home.get("html") or "")
        pages.append((home_url, str(home.get("title") or ""), home_html))
        found.extend((item, home_url) for item in signals.detect_home(home_html, domain, self.confirmed))
        budget = self.settings.pages_per_lead if self.settings else 5
        reads = 1

        async def read_more(path: str) -> dict[str, Any] | None:
            nonlocal reads
            if reads >= budget:
                return None
            reads += 1
            got_status, page, note = await self._fetch(base + path, lead.id)
            if got_status == "capped":
                raise _Capped(note)
            if page is not None:
                pages.append((str(page.get("final_url") or base + path), str(page.get("title") or ""), str(page.get("html") or "")))
            return page

        try:
            if self.confirmed & signals.NEEDS_ROBOTS:
                robots = await read_more("/robots.txt")
                if robots is not None:
                    found.append((signals.detect_robots(str(robots.get("html") or "")), base + "/robots.txt"))
            if self.confirmed & signals.NEEDS_SITEMAP:
                sitemap = await read_more("/sitemap.xml")
                if sitemap is not None:
                    found.extend((item, base + "/sitemap.xml") for item in signals.detect_sitemap(str(sitemap.get("html") or ""), self.confirmed, date.today()))
            if self.confirmed & signals.NEEDS_CONTACT_PAGE and not any(item.code in signals.NEEDS_CONTACT_PAGE for item, _ in found):
                contact = await read_more(signals.CONTACT_PATHS[0])
                if contact is not None:
                    found.extend((item, str(contact.get("final_url") or base + signals.CONTACT_PATHS[0]))
                                 for item in signals.detect_channels(str(contact.get("html") or ""), domain, self.confirmed))
            if self.confirmed & signals.NEEDS_CAREERS:
                for path in signals.CAREERS_PATHS:
                    careers = await read_more(path)
                    if careers is not None:
                        found.extend((item, str(careers.get("final_url") or base + path)) for item in signals.detect_hiring(str(careers.get("html") or "")))
                        break
        except _Capped as capped:
            self.run.stop_reason = capped.reason

        evidence, observations = await asyncio.to_thread(self._save_profile, lead, account, pages, found)
        self.run.results["observations"] = int(self.run.results.get("observations", 0)) + observations
        self._step("profile", "done", f"{domain}: {len(pages)} page(s) read, {observations} signal(s) recorded.", account.name)

        # The research skill reads the labelled pages and proposes inferences (one model call at most).
        stop = bool(self.run.stop_reason)
        if evidence and not stop:
            if self.run.used.model_calls >= self.run.estimate.model_calls and await asyncio.to_thread(assist.model_connected, self.context):
                self._step("infer", "skipped", "The confirmed number of model calls is used up; no inference was drafted.", account.name)
            else:
                try:
                    if await asyncio.to_thread(assist.model_connected, self.context):
                        self.run.used.model_calls += 1
                    proposed, generator, note = await assist.research_inferences(self.context, account, lead, product, evidence)
                except ModelBudgetExceededError as exc:
                    self.run.stop_reason = exc.message
                    self._step("infer", "stopped", exc.message, account.name)
                    return True
                added = await asyncio.to_thread(self._save_inferences, lead, evidence, proposed, generator)
                self.run.results["inferences"] = int(self.run.results.get("inferences", 0)) + added
                self._step("infer", "done", (f"{added} inference(s) proposed for review." + (f" {note}" if note else ""))[:500], account.name)
        return stop

    def _save_profile(self, lead: Lead, account: Account, pages: list[tuple[str, str, str]], found: list[tuple[signals.Found, str]]) -> tuple[list[Evidence], int]:
        if self.run is None:
            raise RuntimeError("the run is not loaded")
        context, now = self.context, utc_now()
        evidence_by_key: dict[str, Evidence] = {}
        count = 0
        with open_business_store(context) as store:
            current = store.get("leads", lead.id, for_update=True)
            if current is None:
                return [], 0
            existing, _ = store.find("evidence", Query(where={"lead_id": lead.id}, limit=500))
            known = {item.url_key: item for item in existing}
            for url, title, html in pages:
                key = url_key(url)
                if key in evidence_by_key:
                    continue
                if key in known:
                    evidence_by_key[key] = known[key]
                    continue
                excerpt = signals.meta_description(html) or signals.visible_text(html, 600)
                item = Evidence(
                    id=new_id("bevi"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, lead_id=lead.id, account_id=account.id,
                    url=url, url_key=key, title=title or url, excerpt=excerpt[:1200], confidence=Confidence.HIGH,
                    source=RecordSource.RESEARCH, research_run_id=self.run.id, created_by=context.user_id, created_at=now, updated_at=now,
                )
                store.add("evidence", item)
                evidence_by_key[key] = item
                known[key] = item
            seen: set[tuple[str, str, str]] = set()
            for detection, page in found:
                if detection.code not in self.confirmed:
                    continue  # only confirmed signals are ever stored
                marker = (detection.code, detection.value_text, url_key(page))
                if marker in seen:
                    continue
                seen.add(marker)
                source = evidence_by_key.get(url_key(page))
                store.add("observations", Observation(
                    id=new_id("bobs"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, lead_id=lead.id, account_id=account.id,
                    run_id=self.run.id, evidence_id=source.id if source else None, signal_code=detection.code, value_num=detection.value_num,
                    value_text=detection.value_text[:300], url=page, excerpt=detection.excerpt[:600], observed_on=now.date(),
                    confidence=detection.confidence, created_by=context.user_id, created_at=now, updated_at=now,
                ))
                count += 1
            if pages:
                if current.state == LeadState.DISCOVERED:
                    stamp_state(current, LeadState.RESEARCHED, now)
                current.last_verified_at = now
                current.updated_at = now
                store.save("leads", current)
            record_activity(store, context, "profiled", f"Profile: {len(pages)} page(s) read, {count} signal(s) recorded.", lead_id=lead.id,
                            account_id=account.id, metadata={"run_id": self.run.id, "pages": len(pages), "observations": count})
        return list(evidence_by_key.values()), count

    def _save_inferences(self, lead: Lead, evidence: list[Evidence], proposed: list[Any], generator: str) -> int:
        now = utc_now()
        with open_business_store(self.context) as store:
            if store.get("leads", lead.id) is None:
                return 0
            for item in proposed:
                store.add("inferences", Inference(
                    id=new_id("binf"), tenant_id=self.context.tenant_id, workspace_id=self.context.workspace_id, lead_id=lead.id,
                    statement=item.statement, criterion=item.criterion, confidence=item.confidence,
                    basis_evidence_ids=[evidence[index].id for index in item.evidence if 0 <= index < len(evidence)],
                    generator=generator, created_by="agent:business_profile", created_at=now, updated_at=now,
                ))
        return len(proposed)


class _Capped(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def execute(context: TenantContext, run_id: str) -> None:
    """Run a confirmed run to the end (or its stop). Safe to call again on an interrupted run."""
    runner = _Runner(context, run_id)
    try:
        if not await runner.load():
            return
        if runner.run is not None and runner.run.kind == RunKind.DISCOVER:
            await runner.discover()
        else:
            await runner.profile()
        await runner.finish()
    except Exception as exc:  # the run is marked failed; the error class only, never page content
        logger.warning("business_pipeline_failed error_class=%s", type(exc).__name__, extra={"anum_business": {"event": "pipeline_failed", "error_class": type(exc).__name__}})
        if runner.run is not None:
            await runner.finish(error=f"The run failed ({type(exc).__name__}); what it found so far is kept.")
