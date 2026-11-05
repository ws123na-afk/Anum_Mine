"""HTTP routes of the books' second release under ``/api/v1/business/books`` (docs/business-books.md#api).

* ``GET /group``, ``GET /group.csv``: the consolidated P&L, balances and cash flow across every business
  where the caller holds ``business:manage`` (``business:manage`` here too). Each other business is
  checked again (an active membership with manage rights, in the same tenant, the workspace not
  archived) before anything of it is read; one the caller cannot manage answers ``403
  workspace_not_allowed`` when asked for, and is never read otherwise.
* ``/recurring``: recurring bills (``business:read`` to read, ``business:write`` to change, like bills).
* ``/rates/auto``: the opt-in to automatic ECB rates (``business:read`` to read, ``business:manage`` to
  change or fetch now, like rates).

Refusals answer ``{"detail": "<plain words>", "code": "<code>"}``. Audit rows hold ids and codes only.
Every route is a plain ``def``; a fetch of the rates happens before any store is opened.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field

from ..authorization import Permission
from ..dependencies import has_permission, repository_context, tenant_context
from ..finance import fx
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import books_group as G
from . import books_rates_auto as auto
from . import books_report as R
from . import books_schedule as schedule
from . import books_service as S
from .api_books import MAX_SPAN_DAYS, _audit, _csv, _found, _manage, _page, _read, _stale, _today, _write, bill_view, rates_since, refusals
from .books_models import Basis, BillTemplate, Frequency, RateFeed, Section, TemplateStatus, dump
from .books_recurring import materialize, resume, template_from
from .books_store import MAX_PAGE, MAX_SCAN, open_books_store

router = APIRouter(prefix="/api/v1/business/books", tags=["business"])

GROUP_DENIED = "Only an owner of this business can see the combined books of the businesses they own."
NOT_ALLOWED = "You can combine only businesses where you are an owner."
MAX_GROUP = 20


# -- consolidated books -----------------------------------------------------------------------------------------------


def _candidates(context: TenantContext) -> list[tuple[str, str]]:
    """This business and every other one where the caller is an owner (``business:manage``), each checked again."""
    out = [(context.workspace_id, S.own_name(context))]
    for item in S.counterparties(context):
        name = S.manageable_workspace(context, item["workspace_id"])
        if name is not None:
            out.append((item["workspace_id"], name))
    return out


def _has_books(context: TenantContext, workspace_id: str) -> bool:
    """Whether a business the caller owns has its book settings (read in that business's own RLS scope)."""
    with open_books_store(context.model_copy(update={"workspace_id": workspace_id})) as store:
        return store.get_settings() is not None


def _group(context: TenantContext, start: date | None, end: date | None, basis: Basis | None, currency: str | None,
           chosen: list[str] | None) -> dict[str, Any]:
    _manage(context)
    candidates = _candidates(context)
    allowed = dict(candidates)
    if chosen:
        wanted = list(dict.fromkeys(chosen))
        if any(workspace not in allowed for workspace in wanted):
            # The same answer for a business that does not exist, is in another organisation or that the caller cannot manage.
            raise S.BooksError(403, NOT_ALLOWED, "workspace_not_allowed")
        if len(wanted) > MAX_GROUP:
            raise S.BooksError(422, f"Choose at most {MAX_GROUP} businesses for one view.", "too_many_businesses", limit=MAX_GROUP)
    # Only businesses with books count towards the limit; the ones without are listed apart.
    ready = [(workspace, name) for workspace, name in candidates if _has_books(context, workspace)]
    ready_ids = {workspace for workspace, _ in ready}
    skipped: list[dict[str, str]] = [{"workspace_id": w, "name": n, "reason": "books_not_set_up"} for w, n in candidates if w not in ready_ids]
    excluded: list[dict[str, str]] = []
    if chosen:
        selected = [(workspace, allowed[workspace]) for workspace in wanted if workspace in ready_ids]
        skipped = [item for item in skipped if item["workspace_id"] in wanted]
    else:
        # Never a silent cut: what does not fit in one view is named, and the owner chooses.
        selected = ready[:MAX_GROUP]
        excluded = [{"workspace_id": w, "name": n, "reason": "limit"} for w, n in ready[MAX_GROUP:]]
    members: list[G.Member] = []
    for workspace, name in selected:
        scoped = context.model_copy(update={"workspace_id": workspace})
        try:
            inputs = S.gather(scoped, rates_since(start, end))
        except S.BooksError as exc:
            if exc.code != "books_not_set_up":
                raise
            skipped.append({"workspace_id": workspace, "name": name, "reason": "books_not_set_up"})
            continue
        members.append(G.Member(workspace_id=workspace, name=name, inputs=inputs))
    anchor = next((m for m in members if m.workspace_id == context.workspace_id), members[0] if members else None)
    choices = {"businesses": [{"workspace_id": m.workspace_id, "name": m.name, "base_currency": m.inputs.settings.base_currency} for m in members],
               "skipped": skipped, "excluded": excluded, "limit": MAX_GROUP, "available": [{"workspace_id": w, "name": n} for w, n in ready]}
    if len(members) < 2 or anchor is None:
        return {"enough": False, **choices}
    reporting = S.currency_of(currency) if currency else anchor.inputs.settings.base_currency
    first, last = R.default_period(anchor.inputs.settings, _today())
    start, end = start or first, end or last
    if start > end:
        raise S.BooksError(422, "The period starts after it ends.", "invalid_period")
    if (end - start).days > MAX_SPAN_DAYS:
        raise S.BooksError(422, "A report covers at most three years.", "period_too_long")
    figures = G.consolidate(members, start, end, basis or anchor.inputs.settings.basis, reporting, _today())
    return {"enough": True, **choices, **figures}


@router.get("/group")
@refusals
def get_group(start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"), basis: Basis | None = None,
              currency: str | None = Query(default=None, pattern=r"^[A-Za-z]{3}$"),
              workspace: list[str] | None = Query(default=None, max_length=200),
              context: TenantContext = Depends(tenant_context)) -> Any:
    """One P&L, the balances and the cash flow of the caller's businesses in a reporting currency, with eliminations."""
    _manage(context)
    return _group(context, start, end, basis, currency, workspace)


@router.get("/group.csv")
@refusals
def export_group(start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"), basis: Basis | None = None,
                 currency: str | None = Query(default=None, pattern=r"^[A-Za-z]{3}$"),
                 workspace: list[str] | None = Query(default=None, max_length=200),
                 context: TenantContext = Depends(tenant_context)) -> Response:
    _manage(context)
    data = _group(context, start, end, basis, currency, workspace)
    if not data["enough"]:
        raise S.BooksError(409, "Combined books need at least two businesses with their books set up.", "not_enough_businesses")
    names = [b["name"] or b["workspace_id"] for b in data["businesses"]]
    ids = [b["workspace_id"] for b in data["businesses"]]
    rows: list[list[Any]] = [["section", "line", *names, "eliminations", "total", "currency"],
                             ["period", f"{data['period']['from']} to {data['period']['to']}", *[""] * len(ids), "", data["period"]["basis"], ""]]
    for section in ("pnl", "balances", "cash_flow"):
        for row in data[section]:
            rows.append([section, row["key"], *[row["businesses"][w] for w in ids], row["eliminations"], row["total"], data["currency"]])
    by_id = dict(zip(ids, names, strict=True))
    for item in data["excluded"]:
        rows.append(["excluded", item["workspace_id"], *[""] * len(ids), "", f"{item['name']}: more businesses than one view holds ({data['limit']})", ""])
    for section, items in data["not_converted"].items():
        for item in items:
            rows.append(["not_converted", f"{section} {item['row']}", *[item["amount"] if item["workspace_id"] == w else "" for w in ids], "",
                         item["amount"] if item["workspace_id"] is None else "", item["currency"]])
    for conversion in data["conversions"]:
        rows.append(["rate", f"{by_id.get(conversion['workspace_id'], '')}: 1 {conversion['from_currency']} = {conversion['rate']} "
                     f"{conversion['to_currency']} on {conversion['as_of']}", *[""] * len(ids), "", conversion["source"], ""])
    return _csv(rows, f"group-pnl-{data['period']['from']}-{data['period']['to']}.csv")


# -- recurring bills ----------------------------------------------------------------------------------------------------


class TemplateInput(BaseModel):
    vendor_id: str = Field(max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    reference: str = Field(default="", max_length=80)
    description: str = Field(min_length=2, max_length=300)
    currency: str = Field(min_length=3, max_length=3)
    subtotal: str = Field(min_length=1, max_length=30)
    tax: str = Field(default="0", max_length=30)
    category: str | None = Field(default=None, max_length=40)
    section: Section | None = None
    cost_centre_id: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    project_id: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    useful_life_months: int | None = Field(default=None, ge=1, le=600)
    frequency: Frequency
    interval: int = Field(default=1, ge=1, le=366)
    start_date: date
    due_days: int = Field(default=0, ge=0, le=366)
    end_date: date | None = None
    max_count: int | None = Field(default=None, ge=1, le=1000)
    create_as: Literal["open", "draft"] = "open"


class TemplateUpdate(TemplateInput):
    version: int


class TemplateAction(BaseModel):
    version: int


def template_view(context: TenantContext, template: BillTemplate, vendors: dict[str, str], today: date) -> dict[str, Any]:
    data = dump(template)
    write = has_permission(context, Permission.BUSINESS_WRITE)
    data.update({
        "vendor_name": vendors.get(template.vendor_id, ""),
        "per_month": str(schedule.per_month(template)),
        "upcoming": [day.isoformat() for day in schedule.upcoming(template, today + timedelta(days=366), limit=3)],
        "can": {"edit": write and template.status != TemplateStatus.ENDED, "pause": write and template.status == TemplateStatus.ACTIVE,
                "resume": write and template.status == TemplateStatus.PAUSED, "end": write and template.status != TemplateStatus.ENDED},
    })
    return data


def _vendors(store: Any) -> dict[str, str]:
    rows, _ = store.find("vendors", limit=MAX_SCAN)
    return {row.id: row.name for row in rows}


def _template_detail(context: TenantContext, template_id: str) -> dict[str, Any]:
    today = _today()
    allocations = S.allocations_by_id(context)
    live = {key for key, row in allocations.items() if row.status == "active"}
    with open_books_store(context) as store:
        template = _found(store, "templates", template_id, "Recurring bill")
        vendors = _vendors(store)
        bills, total = store.find("bills", where={"template_id": template.id}, order_by="bill_date", limit=24)
        ids = [bill.id for bill in bills]
        payments, _ = store.find("bill_payments", where={"bill_id": ids, "voided_at": None}, limit=MAX_SCAN) if ids else ([], 0)
    by_bill: dict[str, list[Any]] = {}
    for payment in payments:
        by_bill.setdefault(payment.bill_id, []).append(payment)
    return {**template_view(context, template, vendors, today),
            "bills": [bill_view(context, bill, by_bill.get(bill.id, []), live, vendors, today, holding=set(allocations)) for bill in bills],
            "bills_total": total}


@router.get("/recurring")
@refusals
def list_recurring(status_filter: str | None = Query(default=None, alias="status", pattern=r"^(active|paused|ended)$"),
                   q: str | None = Query(default=None, max_length=120), limit: int = Query(default=25, ge=1, le=MAX_PAGE),
                   offset: int = Query(default=0, ge=0), context: TenantContext = Depends(tenant_context)) -> Any:
    """Recurring bills with what they cost a month and their next bills; ``upcoming`` lists the bills due in the next 90 days."""
    _read(context)
    today = _today()
    with open_books_store(context) as store:
        rows, total = store.find("templates", where={"status": status_filter} if status_filter else None, text=(q or "").strip() or None,
                                 text_fields=("description", "reference", "category"), order_by="created_at", limit=limit, offset=offset)
        everything, _ = store.find("templates", where={"status": "active"}, limit=MAX_SCAN)
        vendors = _vendors(store)
    return {**_page([template_view(context, row, vendors, today) for row in rows], total, limit, offset),
            "upcoming": R.upcoming_bills(everything, vendors, today)}


@router.post("/recurring", status_code=status.HTTP_201_CREATED)
@refusals
def create_recurring(payload: TemplateInput, context: TenantContext = Depends(tenant_context),
                     repository: AnumRepository = Depends(repository_context)) -> Any:
    """A recurring bill. Bills already due (a first date today or earlier) are made at once; the daily job makes the rest."""
    _write(context)
    now, today = utc_now(), _today()
    with open_books_store(context) as store:
        S.settings_or_409(store)
        template = template_from(context, store, payload.model_dump(), now=now, today=today)
        store.add("templates", template)
        _, made = materialize(store, template.id, today=today, now=now, actor=context.user_id)
    _audit(repository, context, "recurring_created", template.id, {"frequency": template.frequency.value, "bills_made": len(made)})
    return _template_detail(context, template.id)


@router.get("/recurring/{template_id}")
@refusals
def get_recurring(template_id: str, context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    return _template_detail(context, template_id)


@router.patch("/recurring/{template_id}")
@refusals
def update_recurring(template_id: str, payload: TemplateUpdate, context: TenantContext = Depends(tenant_context),
                     repository: AnumRepository = Depends(repository_context)) -> Any:
    """Changes the bills it makes from now on; a bill already made keeps its figures."""
    _write(context)
    now, today = utc_now(), _today()
    with open_books_store(context) as store:
        current = _found(store, "templates", template_id, "Recurring bill", for_update=True)
        if current.version != payload.version:
            raise _stale()
        updated = template_from(context, store, payload.model_dump(exclude={"version"}), now=now, today=today, existing=current)
        if not store.save("templates", updated, expected_version=current.version):
            raise _stale()
        materialize(store, template_id, today=today, now=now, actor=context.user_id)
    _audit(repository, context, "recurring_updated", template_id)
    return _template_detail(context, template_id)


def _act(context: TenantContext, repository: AnumRepository, template_id: str, version: int, action: str) -> dict[str, Any]:
    _write(context)
    now, today = utc_now(), _today()
    with open_books_store(context) as store:
        current = _found(store, "templates", template_id, "Recurring bill", for_update=True)
        if current.version != version:
            raise _stale()
        if current.status == TemplateStatus.ENDED:
            raise S.BooksError(409, "This recurring bill has ended.", "template_ended")
        if action == "pause":
            if current.status != TemplateStatus.ACTIVE:
                raise S.BooksError(409, "Only an active recurring bill is paused.", "not_active")
            item = current.model_copy(update={"status": TemplateStatus.PAUSED, "updated_by": context.user_id, "updated_at": now, "version": current.version + 1})
        elif action == "resume":
            if current.status != TemplateStatus.PAUSED:
                raise S.BooksError(409, "Only a paused recurring bill is resumed.", "not_paused")
            item = resume(current, today=today, now=now, actor=context.user_id)
        else:
            item = current.model_copy(update={"status": TemplateStatus.ENDED, "next_date": None, "end_reason": "owner", "ended_at": now,
                                              "updated_by": context.user_id, "updated_at": now, "version": current.version + 1})
        if not store.save("templates", item, expected_version=current.version):
            raise _stale()
        if action == "resume":
            materialize(store, template_id, today=today, now=now, actor=context.user_id)
    _audit(repository, context, f"recurring_{action}", template_id)
    return _template_detail(context, template_id)


@router.post("/recurring/{template_id}/pause")
@refusals
def pause_recurring(template_id: str, payload: TemplateAction, context: TenantContext = Depends(tenant_context),
                    repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    return _act(context, repository, template_id, payload.version, "pause")


@router.post("/recurring/{template_id}/resume")
@refusals
def resume_recurring(template_id: str, payload: TemplateAction, context: TenantContext = Depends(tenant_context),
                     repository: AnumRepository = Depends(repository_context)) -> Any:
    """Active again from the first period on or after today; periods passed while paused are not made."""
    _write(context)
    return _act(context, repository, template_id, payload.version, "resume")


@router.post("/recurring/{template_id}/end")
@refusals
def end_recurring(template_id: str, payload: TemplateAction, context: TenantContext = Depends(tenant_context),
                  repository: AnumRepository = Depends(repository_context)) -> Any:
    """Stops for good; bills already made stay as they are."""
    _write(context)
    return _act(context, repository, template_id, payload.version, "end")


# -- automatic exchange rates ---------------------------------------------------------------------------------------------


class FeedInput(BaseModel):
    enabled: bool
    currencies: list[str] = Field(default_factory=list, max_length=auto.MAX_CURRENCIES)
    version: int | None = None


def _feed_view(context: TenantContext, feed: RateFeed | None, base: str | None) -> dict[str, Any]:
    data = dump(feed) if feed else {"enabled": False, "currencies": [], "last_attempt_at": None, "last_success_at": None, "last_as_of": None,
                                    "last_error": "", "unavailable": [], "version": None}
    data["followed"] = auto.followed(feed, base) if feed else ([base] if base and base != "EUR" else [])
    data["source"] = auto.SOURCE
    data["source_url"] = fx.ECB_URL
    data["can_manage"] = has_permission(context, Permission.BUSINESS_MANAGE)
    return data


@router.get("/rates/auto")
@refusals
def get_rate_feed(context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    with open_books_store(context) as store:
        feed = store.first("rate_feeds")
        settings = store.get_settings()
    return _feed_view(context, feed, settings.base_currency if settings else None)


@router.put("/rates/auto")
@refusals
def put_rate_feed(payload: FeedInput, context: TenantContext = Depends(tenant_context),
                  repository: AnumRepository = Depends(repository_context)) -> Any:
    """Turn automatic ECB rates on or off and choose the currencies followed (the base currency is always followed)."""
    _manage(context)
    codes = sorted({S.currency_of(code) for code in payload.currencies})
    now = utc_now()
    with open_books_store(context) as store:
        settings = S.settings_or_409(store)
        current = store.first("rate_feeds")
        if (current.version if current else None) != payload.version:
            raise _stale()
        if current is None:
            feed = RateFeed(id=new_id("bfeed"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, enabled=payload.enabled,
                            currencies=codes, updated_by=context.user_id, updated_at=now)
            store.add("rate_feeds", feed)
        else:
            feed = current.model_copy(update={"enabled": payload.enabled, "currencies": codes, "updated_by": context.user_id, "updated_at": now,
                                              "version": current.version + 1})
            if not store.save("rate_feeds", feed, expected_version=current.version):
                raise _stale()
    _audit(repository, context, "rate_feed_saved", feed.id, {"enabled": feed.enabled, "currencies": len(codes)})
    return _feed_view(context, feed, settings.base_currency)


@router.post("/rates/auto/refresh")
@refusals
def refresh_rate_feed(context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    """Fetch today's ECB rates now for this business (at most every ten minutes). A failure keeps the rates already stored."""
    from .books_jobs import refresh_feed

    _manage(context)
    now = utc_now()
    with open_books_store(context) as store:
        feed = store.first("rate_feeds")
        settings = store.get_settings()
    if feed is None or not feed.enabled:
        raise S.BooksError(409, "Turn automatic rates on first.", "feed_off")
    if feed.last_attempt_at is not None and (now - feed.last_attempt_at).total_seconds() < auto.REFRESH_EVERY_SECONDS:
        raise S.BooksError(429, "The rates were fetched a few minutes ago. Try again later.", "too_soon")
    published, error = None, None
    try:
        published = auto.fetch(now.date())  # the network read happens with no store open
    except auto.FeedError as exc:
        error = str(exc)
    added = refresh_feed(context, published, error, now=now, actor=context.user_id)
    _audit(repository, context, "rate_feed_refreshed", feed.id, {"added": added, "failed": error is not None})
    with open_books_store(context) as store:
        fresh = store.first("rate_feeds")
    return {**_feed_view(context, fresh, settings.base_currency if settings else None), "added": added}


__all__ = ["router"]
