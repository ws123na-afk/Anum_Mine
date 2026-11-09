"""Attribution suggestions: what else a payment is (docs/finance.md#attribution-suggestions).

Producers for the one suggestion queue (``suggestions.register_producer``) that propose, for a recorded payment, the
record it belongs to in another module, each with a confidence and the reason in words:

* **business** (personal or business treatment, project): the owner allocated payments to the same merchant to a
  business before (the same merchant key, ``rules.merchant_key`` with the owner's aliases): the same business, the
  same treatment and project as the last time. The cost centre the business gave that allocation is named in the
  evidence; classifying the row stays the business's own step in Books, Allocations.
* **vehicle**: a fuel, parking, toll or service payment and a fuel entry or service record of the vehicles module with
  the same amount and currency within three days (seven for a service), not linked to a payment yet.
* **purchase**: a purchase of the purchases module with the same total and currency within seven days, not paid yet;
  merchant words that agree make it strong.
* **debt**: a payment whose words name one of the owner's debts (or pays its minimum exactly).
* **bill and installment**: the bills producer keeps its ``payment_match`` item and, through :func:`bill_link`, uses the
  one payment matcher (``commitments_api.find_candidates``, the matcher behind ``GET /payment-candidates``) to propose the
  payment; installment plans are bills, so their due dates come the same way. No second matcher.
* **fx_fee**: a foreign card purchase whose FX fee or markup can be separated (``card_fx``); Review opens the drawer.

Accept on a ``link`` action creates the link through ``finance.links`` (``create`` or ``allocate``), so ownership,
currency, the amount left, single payment targets and the vets all apply; nothing is linked automatically (no
automation policy can link, ``rules.require_automatable``). Producers only read, each in its own short unit of work.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import timedelta
from decimal import Decimal
from typing import Any

from . import suggestions as SG
from . import txn_types as TT
from .merchant_match import merchants_agree
from .models import Transaction, TransactionType

RECENT_DAYS = 60
VEHICLE_WORDS = re.compile(
    r"\b(fuel|petrol|gasoline|diesel|adnoc|enoc|eppco|emarat|aldrees|sasco|naft|petromin|parking|mawaqif|salik|darb|toll|car\s*wash|"
    r"tyre|tire|service\s*cent(?:er|re)|oil\s*change|garage|workshop)\b|وقود|بنزين|محطة|مواقف|صيانة|ورشة|اطارات|إطارات",
    re.IGNORECASE,
)
SERVICE_WORDS = re.compile(r"\b(service|garage|workshop|tyre|tire|oil)\b|صيانة|ورشة|اطارات|إطارات", re.IGNORECASE)


def _spend_rows(snap: SG.Snapshot, days: int = RECENT_DAYS, *, debts: bool = False) -> list[Transaction]:
    """Money out of the last ``days`` that is spending or not filed yet (uncategorized); a loan repayment too for debts."""
    since = snap.today - timedelta(days=days)
    wanted = TT.SPEND_TYPES | {TransactionType.UNCATEGORIZED} | ({TransactionType.LOAN_PRINCIPAL} if debts else set())
    out = []
    for t in snap.rows:
        if t.posted_on < since or t.amount >= 0 or t.id not in snap.canon:
            continue
        account = snap.accounts.get(t.account_id)
        if TT.typed(t, account.kind if account else None).type not in wanted:
            continue
        out.append(t)
    return out


def _links(snap: SG.Snapshot) -> list[Any]:
    from .links_store import LinkStatus, open_link_stores

    with open_link_stores(snap.context) as (_finance, _sms, links):
        rows, _ = links.list_links(snap.context, canonical_id=None, dimension=None, target_type=None, target_id=None, status=None,
                                   limit=20_000, offset=0)
    return [l for l in rows if l.status != LinkStatus.VOID]


def _suggest(t: Transaction, canonical: str, key: str, kind: str, detected: str, why: str, *, params: dict[str, str], evidence: list[SG.Evidence],
             confidence: Decimal, link: SG.LinkProposal | None, href: str) -> SG.Suggestion:
    return SG.Suggestion(
        key=key[:200], producer="attribution", kind="attribution", detected=detected, why=why, params={"dimension": kind, **params},
        evidence=[SG._txn(t), *evidence][:20], confidence=confidence, effect=SG._out(t),
        action=SG.ProposedAction(kind="link" if link else "open", transaction_ids=[t.id], link=link), href=href, occurred_on=t.posted_on,
    )


# -- business ----------------------------------------------------------------------------------------------------------


def business(snap: SG.Snapshot, live: list[Any]) -> list[SG.Suggestion]:
    from . import links as K

    by_canonical_row = {snap.canon[t.id]: t for t in snap.rows if t.id in snap.canon}
    history: dict[str, Counter[str]] = {}
    last: dict[tuple[str, str], Any] = {}
    allocated = set()
    for link in sorted((l for l in live if l.dimension == "business"), key=lambda l: l.created_at):
        allocated.add(link.canonical_id)
        row = by_canonical_row.get(link.canonical_id)
        if row is None:
            continue
        merchant = snap.aliases.key(row.description)
        if not merchant:
            continue
        history.setdefault(merchant, Counter())[link.target_id] += 1
        last[(merchant, link.target_id)] = link
    if not history:
        return []
    allowed: dict[str, bool] = {}
    allocations: dict[str, dict[str, Any]] = {}
    from ..membership_directory import list_caller_memberships

    names = {row.workspace_id: row.workspace_name or row.workspace_id for row in list_caller_memberships(snap.context)}
    out = []
    for t in _spend_rows(snap):
        canonical = snap.canon[t.id]
        if canonical in allocated:
            continue
        merchant = snap.aliases.key(t.description)
        seen = history.get(merchant or "")
        if not seen:
            continue
        workspace, agree = seen.most_common(1)[0]
        total = sum(seen.values())
        if workspace not in allowed:
            allowed[workspace] = K.can_allocate_to(snap.context, workspace)
        if not allowed[workspace]:
            continue
        if workspace not in allocations:
            allocations[workspace] = _allocations(snap, workspace)
        previous = allocations[workspace].get(last[(merchant or "", workspace)].id)
        confidence = min(Decimal("0.9"), Decimal("0.5") + Decimal("0.4") * Decimal(agree) / Decimal(total) * min(Decimal(agree), Decimal(3)) / 3)
        treatment = getattr(previous, "treatment", None) or "owner_contribution"
        project = getattr(previous, "project_id", None)
        params = {"business": workspace, "business_name": names.get(workspace, workspace)[:80], "agree": str(agree), "seen": str(total),
                  "treatment": treatment, "merchant": merchant or ""}
        if project:
            params["project"] = project
        evidence = [SG.Evidence(label="Your earlier allocation", ref_type="business", ref_id=last[(merchant or "", workspace)].canonical_id)]
        centre = allocations[workspace].get(f"centre:{getattr(previous, 'id', '')}")
        if centre:
            params["cost_centre"] = centre
            evidence.append(SG.Evidence(label="Cost centre the business used", ref_type="other", value=centre[:200]))
        out.append(_suggest(
            t, canonical, f"attribution:business:{t.id}:{workspace}:{project or '-'}", "business",
            f"You allocated {merchant} to a business {agree} of {total} times.",
            "The same merchant was a business cost before. Accept to allocate it the same way; the business sees only the allocation.",
            params=params, evidence=evidence, confidence=confidence.quantize(Decimal("0.01")),
            link=SG.LinkProposal(canonical_id=canonical, dimension="business", target_type="business_workspace", target_id=workspace,
                                 amount=-t.amount, currency=t.currency, treatment=treatment, category=t.category.value, project_id=project),
            href="#finance-transactions",
        ))
    return out


def _allocations(snap: SG.Snapshot, workspace: str) -> dict[str, Any]:
    """The business's allocation rows made from this owner's links (by source link id), and the cost centre each was given."""
    from .links_store import open_workspace_allocations

    there = snap.context.model_copy(update={"workspace_id": workspace})
    out: dict[str, Any] = {}
    try:
        with open_workspace_allocations(there) as links:
            rows, _ = links.list_allocations(there, currency=None, limit=5_000, offset=0)
        mine = [r for r in rows if r.allocated_by == snap.context.user_id]
        out.update({r.source_link_id: r for r in mine})
        from ..business.books_store import open_books_store

        with open_books_store(there) as store:
            tags, _ = store.find("allocation_tags", where={"allocation_id": {r.id for r in mine}}, limit=5_000) if mine else ([], 0)
            centres = {c.id: c for c in store.find("cost_centres", limit=500)[0]}
        by_alloc = {r.id: r.source_link_id for r in mine}
        for tag in tags:
            centre = centres.get(tag.cost_centre_id or "")
            if centre is not None and tag.allocation_id in by_alloc:
                out[f"centre:{by_alloc[tag.allocation_id]}"] = f"{centre.code} {centre.name}"
    except Exception:  # noqa: BLE001 - the business's details only add to the explanation; the suggestion stands without them
        return out
    return out


# -- vehicles and purchases ------------------------------------------------------------------------------------------------


def vehicles(snap: SG.Snapshot, live: list[Any]) -> list[SG.Suggestion]:
    from ..vehicles.store import open_vehicle_store

    linked = {l.canonical_id for l in live if l.dimension == "vehicle"}
    rows = [t for t in _spend_rows(snap) if snap.canon[t.id] not in linked and VEHICLE_WORDS.search(t.description or "")]
    if not rows:
        return []
    since = snap.today - timedelta(days=RECENT_DAYS + 7)
    with open_vehicle_store(snap.context) as store:
        vehicles_, _ = store.list_vehicles(snap.context, query=None, archived=False, limit=50, offset=0)
        fuel, _ = store.list_records(snap.context, "fuel", None, query=None, start=since, end=None, limit=2_000, offset=0)
        service, _ = store.list_records(snap.context, "service", None, query=None, start=since, end=None, limit=2_000, offset=0)
    names = {v.id: (v.nickname or v.registration) for v in vehicles_}
    open_fuel = [f for f in fuel if (f.payment is None or f.payment.status == "void") and f.total is not None and f.owner_user_id == snap.context.user_id]
    open_service = [s for s in service if (s.payment is None or s.payment.status == "void") and s.receipt_total is not None
                    and s.owner_user_id == snap.context.user_id]
    taken: set[str] = set()
    out = []
    for t in rows:
        amount = -t.amount
        found = None
        for record, day, total, target, window in (
                [(f, f.filled_at.date(), f.total, "vehicle_fuel_entry", 3) for f in open_fuel]
                + [(s, s.service_on, s.receipt_total, "vehicle_service_record", 7) for s in open_service]):
            if record.id in taken or record.currency != t.currency or total != amount or abs((day - t.posted_on).days) > window:
                continue
            found = (record, target, day)
            break
        canonical = snap.canon[t.id]
        if found is not None:
            record, target, day = found
            taken.add(record.id)
            vehicle = names.get(record.vehicle_id, "")
            kind = "fuel entry" if target == "vehicle_fuel_entry" else "service record"
            out.append(_suggest(
                t, canonical, f"attribution:vehicle:{t.id}:{record.id}", "vehicle", f"This payment may be the {kind} of {vehicle} on {day.isoformat()}.",
                "Same amount and currency within a few days, and the words say fuel, parking or a service.",
                params={"vehicle": vehicle[:80], "record": target, "day": day.isoformat()},
                evidence=[SG.Evidence(label=vehicle[:120] or "Vehicle", ref_type="vehicle", ref_id=record.vehicle_id, value=f"{day.isoformat()} {record.currency} {amount}")],
                confidence=Decimal("0.85"),
                link=SG.LinkProposal(canonical_id=canonical, dimension="vehicle", target_type=target, target_id=record.id, amount=amount, currency=t.currency),
                href="#vehicles",
            ))
        elif len(vehicles_) == 1 and t.posted_on >= snap.today - timedelta(days=30):
            vehicle = names[vehicles_[0].id]
            service_like = bool(SERVICE_WORDS.search(t.description or ""))
            out.append(_suggest(
                t, canonical, f"attribution:vehicle_record:{t.id}", "vehicle",
                f"This looks like a {'service' if service_like else 'fuel or parking'} cost of {vehicle}.",
                "The words say fuel, parking or a service and you have one vehicle. Record it on the vehicle to track its cost.",
                params={"vehicle": vehicle[:80], "record": "none"},
                evidence=[SG.Evidence(label=vehicle[:120], ref_type="vehicle", ref_id=vehicles_[0].id)], confidence=Decimal("0.5"), link=None,
                href="#vehicles",
            ))
    return out


def purchases(snap: SG.Snapshot, live: list[Any]) -> list[SG.Suggestion]:
    from ..purchases.models import PAYMENT_TARGET
    from ..purchases.store import open_purchase_store

    linked = {l.canonical_id for l in live if l.dimension == "purchase"}
    rows = [t for t in _spend_rows(snap) if snap.canon[t.id] not in linked]
    if not rows:
        return []
    with open_purchase_store(snap.context) as store:
        found, _ = store.list_purchases(snap.context, query=None, status=None, archived=False, limit=500, offset=0)
    unpaid = [p for p in found if p.total is not None and (p.payment is None or p.payment.status == "void") and p.owner_user_id == snap.context.user_id
              and p.ordered_on >= snap.today - timedelta(days=RECENT_DAYS + 7)]
    taken: set[str] = set()
    out = []
    for t in rows:
        amount = -t.amount
        fits = [p for p in unpaid if p.id not in taken and p.currency == t.currency and p.total == amount and abs((p.ordered_on - t.posted_on).days) <= 7]
        if not fits:
            continue
        agreeing = [p for p in fits if merchants_agree(p.merchant, t.description)]
        if len(fits) > 1 and len(agreeing) != 1:
            continue  # two purchases fit and the words do not tell them apart: no suggestion
        purchase = agreeing[0] if agreeing else fits[0]
        taken.add(purchase.id)
        strong = bool(agreeing)
        out.append(_suggest(
            t, snap.canon[t.id], f"attribution:purchase:{t.id}:{purchase.id}", "purchase",
            f"This payment may pay your purchase from {purchase.merchant} on {purchase.ordered_on.isoformat()}.",
            "Same total and currency within a week" + (", and the merchant names agree." if strong else ". The descriptions do not name the shop."),
            params={"merchant": purchase.merchant[:80], "day": purchase.ordered_on.isoformat()},
            evidence=[SG.Evidence(label=purchase.merchant[:120], ref_type="purchase", ref_id=purchase.id, value=f"{purchase.currency} {purchase.total}")],
            confidence=Decimal("0.85") if strong else Decimal("0.6"),
            link=SG.LinkProposal(canonical_id=snap.canon[t.id], dimension="purchase", target_type=PAYMENT_TARGET, target_id=purchase.id,
                                 amount=amount, currency=t.currency),
            href="#purchases",
        ))
    return out


# -- debts and bills (the one payment matcher) --------------------------------------------------------------------------


def debts(snap: SG.Snapshot, live: list[Any]) -> list[SG.Suggestion]:
    from .commitments import DEBT_TARGET
    from .models import Debt
    from .store import open_finance_store

    with open_finance_store(snap.context) as store:
        owed = [d for d in store.list_items(snap.context, "debt") if isinstance(d, Debt) and d.balance > 0]
    if not owed:
        return []
    linked = {l.canonical_id for l in live if l.dimension == "debt"}
    out = []
    for t in _spend_rows(snap, 45, debts=True):
        canonical = snap.canon[t.id]
        if canonical in linked:
            continue
        amount = -t.amount
        named = [d for d in owed if d.currency == t.currency and merchants_agree(d.name, t.description)]
        exact = [d for d in owed if d.currency == t.currency and d.minimum_payment > 0 and d.minimum_payment == amount]
        choice = named if len(named) == 1 else (exact if len(exact) == 1 and not named else [])
        if not choice:
            continue
        debt = choice[0]
        both = debt in named and debt.minimum_payment == amount
        out.append(_suggest(
            t, canonical, f"attribution:debt:{t.id}:{debt.id}", "debt", f"This payment may pay your debt {debt.name}.",
            "The description names the debt" + (" and the amount is its minimum payment." if both else "." if debt in named
                                                else ": the amount is exactly its minimum payment, though the words do not name it."),
            params={"debt": debt.name[:80]}, evidence=[SG.Evidence(label=debt.name[:120], ref_type="debt", ref_id=debt.id)],
            confidence=Decimal("0.85") if both else Decimal("0.7") if debt in named else Decimal("0.55"),
            link=SG.LinkProposal(canonical_id=canonical, dimension="debt", target_type=DEBT_TARGET, target_id=debt.id, amount=amount, currency=t.currency),
            href="#finance-debts",
        ))
    return out


def bill_link(snap: SG.Snapshot, occ: Any) -> tuple[SG.LinkProposal, Decimal, str] | None:
    """For a bill due date with no matched payment: the payment ``find_candidates`` ranks first, when it fits (the same amount, or
    words that agree within 10 %). Answers the link, a confidence and the payment's merchant."""
    from .commitments import BILL_TARGET, bill_target_id
    from .commitments_api import find_candidates

    try:
        found = find_candidates(snap.context, "bill", around=occ.due_on, amount=occ.expected, currency=occ.currency, limit=5)
    except Exception:  # noqa: BLE001 - a payment proposal is extra: the bill item stands without it
        return None
    for cand in found:
        if not -7 <= (cand.posted_on - occ.due_on).days <= 10:
            continue
        words = merchants_agree(occ.name, cand.merchant)
        close = abs(cand.amount - occ.expected) <= occ.expected * Decimal("0.1")
        if words is False or not close:
            continue
        if not (words or cand.amount == occ.expected):
            continue
        confidence = Decimal("0.85") if words and cand.amount == occ.expected else Decimal("0.7") if words else Decimal("0.55")
        amount = min(occ.expected, cand.remaining)
        return (SG.LinkProposal(canonical_id=cand.canonical_id, dimension="bill", target_type=BILL_TARGET,
                                target_id=bill_target_id(occ.bill_id, occ.due_on), amount=amount, currency=occ.currency), confidence, cand.merchant)
    return None


# -- card FX fees ------------------------------------------------------------------------------------------------------


def fx_fees(snap: SG.Snapshot) -> list[SG.Suggestion]:
    """Foreign card purchases whose fee or markup can be separated or linked (Review opens the transaction)."""
    from . import card_fx as C

    since = snap.today - timedelta(days=RECENT_DAYS)
    by_account: dict[str, list[Transaction]] = {}
    for t in snap.rows:
        if t.posted_on >= since - timedelta(days=C.FEE_ROW_DAYS):
            by_account.setdefault(t.account_id, []).append(t)
    out = []
    for t in snap.rows:
        if t.posted_on < since or t.amount >= 0 or not (t.notes or "").strip() and not re.search(r"[A-Z]{3}\s?\d", t.description or ""):
            continue
        rows = [C.Row(id=n.id, account_id=n.account_id, posted_on=n.posted_on, amount=n.amount, currency=n.currency, description=n.description,
                      notes=n.notes, source="statement" if n.import_id else "manual",
                      transaction_type=n.transaction_type.value if n.transaction_type else None, related_transaction_id=n.related_transaction_id)
                for n in by_account.get(t.account_id, [])]
        found = C.details(next(r for r in rows if r.id == t.id), rows)
        if found.split not in ("available", "link") or found.fee is None:
            continue
        out.append(SG.Suggestion(
            key=f"fx_fee:{t.id}:{found.fee.source}:{found.fee.amount}", producer="card_fx", kind="fx_fee",
            detected=f"{found.fee.amount} {found.fee.currency} of this foreign purchase is the bank's FX fee.",
            why=("The fee is a separate statement row: point it at its purchase so both are read together." if found.split == "link"
                 else "The billed amount includes the stated markup or fee, which inflates the purchase. Separate it as its own bank fee line."),
            params={"fee": str(found.fee.amount), "currency": found.fee.currency, "source": found.fee.source,
                    "original": f"{found.original_currency} {found.original_amount}" if found.original_amount else ""},
            evidence=[SG._txn(t)], confidence=Decimal("0.9") if found.fee.source != "fee_row" else Decimal("0.75"),
            effect=[SG.Effect(amount=found.fee.amount, currency=found.fee.currency, direction="out", note="fx_fee")],
            action=SG.ProposedAction(kind="open", transaction_ids=[t.id]), href=f"#finance-transactions?txn={t.id}", occurred_on=t.posted_on,
        ))
    return out


def produce(snap: SG.Snapshot) -> list[SG.Suggestion]:
    live = _links(snap)
    out: list[SG.Suggestion] = []
    for part in (business, vehicles, purchases, debts):
        try:
            out.extend(part(snap, live))
        except Exception:  # noqa: BLE001 - one module that cannot be read hides only its own suggestions
            SG.logger.warning("finance_attribution_part_failed part=%s", part.__name__)
    return out


SG.register_producer("attribution", produce)
SG.register_producer("card_fx", fx_fees)

__all__ = ["bill_link", "business", "debts", "fx_fees", "produce", "purchases", "vehicles"]
