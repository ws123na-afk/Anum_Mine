"""Business books, second release (docs/business-books.md): consolidated books, automatic ECB rates and recurring bills.

In-memory stores; ``test_postgres_business_books_v2.py`` repeats the storage rules, the daily job's
discovery and the races under forced RLS. Customers, vendors and rates are SYNTHETIC.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from test_business_books import (  # noqa: F401  (``clean`` is the autouse fixture that resets every store)
    API,
    BUSINESS,
    MEMBER,
    OTHER,
    OTHER_OWNER,
    OWNER,
    PERSONAL,
    TENANT,
    VIEWER,
    bill,
    clean,
    client,
    report,
    seed_invoice,
    setup_books,
    today,
    vendor,
)

from anum_api.business import books_jobs, books_rates_auto
from anum_api.business import books_schedule as schedule
from anum_api.business.books_models import BillTemplate, Frequency, Rate, RateFeed, RateOrigin, TemplateStatus
from anum_api.business.books_report import Converter
from anum_api.business.books_store import books_memory_store
from anum_api.dependencies import memory_repository
from anum_api.finance import connectors
from anum_api.schemas import WorkspaceMembership, utc_now

THIRD = "ws_books_third"
OWNER_TWO = {**OWNER, "x-user-id": "owner_two"}  # an owner of this business and only a member of the other
THIRD_OWNER = {**OWNER, "x-workspace-id": THIRD}


@pytest.fixture(autouse=True)
def more_people() -> Iterator[None]:
    now = utc_now()
    for workspace, user, role in ((BUSINESS, "owner_two", "owner"), (OTHER, "owner_two", "member"), (THIRD, "owner_books", "owner")):
        memory_repository.store.memberships[(TENANT, workspace, user)] = WorkspaceMembership(
            tenant_id=TENANT, workspace_id=workspace, user_id=user, role=role, active=True, created_at=now, updated_at=now)
    yield


def other_vendor(headers: dict, name: str = "Synthetic Supplier") -> dict:
    response = client.post(f"{API}/vendors", headers=headers, json={"name": name, "default_category": "rent"})
    assert response.status_code == 201, response.text
    return response.json()


def group(headers: dict = OWNER, **params: Any) -> dict:
    response = client.get(f"{API}/group", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def row(data: dict, section: str, key: str) -> dict:
    return next(item for item in data[section] if item["key"] == key)


def two_businesses() -> None:
    """ANUM (SAR) sells 5,000 and pays 1,000 rent; Tyre Pulse (AED) pays 2,000 rent; ANUM recharges Tyre Pulse 1,000 SAR for the
    shared office and lends it 3,000 SAR. Tyre Pulse records 1 AED = 1.02 SAR."""
    setup_books()
    setup_books(OTHER_OWNER, base_currency="AED", country="AE")
    seed_invoice(subtotal="5000", tax="0")
    assert bill(vendor()["id"], subtotal="1000", tax="0").status_code == 201
    assert bill(other_vendor(OTHER_OWNER)["id"], headers=OTHER_OWNER, subtotal="2000", tax="0", currency="AED").status_code == 201
    for kind, amount in (("recharge", "1000"), ("loan", "3000")):
        entry = client.post(f"{API}/intercompany", headers=OWNER, json={"workspace_id": OTHER, "kind": kind, "direction": "receivable",
                                                                        "entry_date": today().isoformat(), "currency": "SAR", "amount": amount,
                                                                        "description": f"Synthetic {kind}"})
        assert entry.status_code == 201, entry.text
    rate = client.post(f"{API}/rates", headers=OTHER_OWNER, json={"base": "AED", "quote": "SAR", "rate": "1.02", "as_of": today().isoformat(),
                                                                  "source": "Synthetic reference"})
    assert rate.status_code == 201, rate.text


# -- consolidated books ---------------------------------------------------------------------------------------------------


def test_consolidation_combines_converts_and_eliminates_intercompany_recharges_and_loans() -> None:
    two_businesses()
    data = group(currency="SAR", workspace=[BUSINESS, OTHER])
    assert data["enough"] is True and data["currency"] == "SAR"
    assert [b["workspace_id"] for b in data["businesses"]] == [BUSINESS, OTHER]
    revenue, costs, other = row(data, "pnl", "revenue"), row(data, "pnl", "operating_expenses"), row(data, "pnl", "other_income")
    assert revenue["businesses"] == {BUSINESS: "5000.00", OTHER: "0.00"} and revenue["total"] == "5000.00"
    # Tyre Pulse's rent converts with its own rate (2,000 AED x 1.02); the recharge is its cost and ANUM's other income.
    assert costs["businesses"] == {BUSINESS: "1000.00", OTHER: "3040.00"}
    assert costs["eliminations"] == "-1000.00" and costs["total"] == "3040.00"
    assert other["businesses"][BUSINESS] == "1000.00" and other["eliminations"] == "-1000.00" and other["total"] == "0.00"
    net = row(data, "pnl", "net_profit")
    assert net["businesses"] == {BUSINESS: "5000.00", OTHER: "-3040.00"} and net["eliminations"] == "0.00" and net["total"] == "1960.00"
    # The loan and the recharge owed between them leave the group's balances; the rent payables stay, converted on the last day.
    receivable, payable = row(data, "balances", "intercompany_receivable"), row(data, "balances", "intercompany_payable")
    assert receivable["businesses"][BUSINESS] == "4000.00" and receivable["total"] == "0.00"
    assert payable["businesses"][OTHER] == "4000.00" and payable["total"] == "0.00"
    assert row(data, "balances", "payables")["total"] == "3040.00"
    assert row(data, "balances", "receivables")["total"] == "5000.00"
    # The loan is cash out of one and into the other: nothing moves for the group.
    lent, borrowed = row(data, "cash_flow", "intercompany_lent"), row(data, "cash_flow", "intercompany_borrowed")
    assert lent["businesses"][BUSINESS] == "3000.00" and lent["total"] == "0.00"
    assert borrowed["businesses"][OTHER] == "3000.00" and borrowed["total"] == "0.00"
    assert row(data, "cash_flow", "net")["total"] == "0.00"
    assert data["complete"] is True and data["eliminated"] is True
    assert {(c["workspace_id"], c["from_currency"], c["source"]) for c in data["conversions"]} == {(OTHER, "AED", "Synthetic reference")}
    # Each business's own books are unchanged by the consolidation.
    assert report()["pnl"]["other_income"]["total"] == "1000.00"


def test_a_figure_without_a_rate_is_listed_not_converted_and_never_summed() -> None:
    two_businesses()
    assert bill(other_vendor(OTHER_OWNER, "Synthetic Euro Supplier")["id"], headers=OTHER_OWNER, subtotal="100", tax="0", currency="EUR").status_code == 201
    data = group(currency="SAR")
    assert data["complete"] is False
    missing = data["not_converted"]["pnl"]
    assert {"workspace_id": OTHER, "row": "operating_expenses", "currency": "EUR", "amount": "100.00"} in missing
    assert {"workspace_id": None, "row": "operating_expenses", "currency": "EUR", "amount": "100.00"} in missing
    assert row(data, "pnl", "operating_expenses")["total"] == "3040.00"  # the euro bill is not in the total
    assert "not_converted" in next(b for b in data["businesses"] if b["workspace_id"] == OTHER)["notes"]
    # In another reporting currency without rates nothing of Tyre Pulse converts, and nothing is invented.
    usd = group(currency="USD")
    assert row(usd, "pnl", "revenue")["total"] == "0.00" and usd["complete"] is False
    assert {item["currency"] for item in usd["not_converted"]["pnl"]} == {"SAR", "AED", "EUR"}


def test_an_intercompany_entry_with_a_business_outside_the_group_is_not_eliminated() -> None:
    two_businesses()
    setup_books(THIRD_OWNER)
    data = group(workspace=[BUSINESS, THIRD])
    assert row(data, "pnl", "other_income")["total"] == "1000.00" and row(data, "pnl", "other_income")["eliminations"] == "0.00"
    assert row(data, "balances", "intercompany_receivable")["total"] == "4000.00"
    assert data["eliminated"] is False


def test_the_group_reads_only_businesses_the_viewer_manages() -> None:
    two_businesses()
    # A member (not an owner) of both businesses, a viewer: refused before anything is read.
    for headers in (MEMBER, VIEWER):
        refused = client.get(f"{API}/group", headers=headers)
        assert refused.status_code == 403, refused.text
    # An owner of this business who is only a member of the other: the other is never part of it, and never named.
    alone = client.get(f"{API}/group", headers=OWNER_TWO)
    assert alone.status_code == 200 and alone.json()["enough"] is False
    assert OTHER not in alone.text and "Synthetic" not in alone.text and "2000" not in alone.text and "3040" not in alone.text
    assert [b["workspace_id"] for b in alone.json()["businesses"]] == [BUSINESS]
    for chosen in ([OTHER], [BUSINESS, OTHER], ["ws_unknown"], [BUSINESS, "ws_in_another_tenant"]):
        asked = client.get(f"{API}/group", headers=OWNER_TWO, params={"workspace": chosen})
        assert asked.status_code == 403, (chosen, asked.text)
        assert asked.json() == {"detail": "You can combine only businesses where you are an owner.", "code": "workspace_not_allowed"}
    assert client.get(f"{API}/group.csv", headers=OWNER_TWO).status_code == 409
    # Another tenant with the same workspace ids sees nothing of this one.
    stranger = {**OWNER, "x-tenant-id": "tenant_other"}
    seen = client.get(f"{API}/group", headers=stranger)
    assert seen.status_code == 200 and seen.json()["enough"] is False and seen.json()["businesses"] == []
    assert OTHER not in seen.text and "5000" not in seen.text


def test_businesses_without_books_are_listed_apart_and_the_csv_has_a_column_each() -> None:
    two_businesses()
    data = group()
    assert {"workspace_id": PERSONAL, "name": PERSONAL, "reason": "books_not_set_up"} in data["skipped"] or any(
        item["workspace_id"] == PERSONAL and item["reason"] == "books_not_set_up" for item in data["skipped"])
    assert {b["workspace_id"] for b in data["businesses"]} == {BUSINESS, OTHER}
    response = client.get(f"{API}/group.csv", headers=OWNER, params={"currency": "SAR", "workspace": [BUSINESS, OTHER]})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0][-3:] == ["eliminations", "total", "currency"] and len(rows[0]) == 2 + 2 + 3
    # A cell that starts with "-" is escaped against spreadsheet formulas, as in every ANUM export.
    assert ["pnl", "net_profit", "5000.00", "'-3040.00", "0.00", "1960.00", "SAR"] in rows
    assert any(r[0] == "rate" and "1 AED = 1.02 SAR" in r[1] for r in rows)
    bad = client.get(f"{API}/group", headers=OWNER, params={"from": "2026-05-01", "to": "2026-01-01"})
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_period"


# -- automatic exchange rates ---------------------------------------------------------------------------------------------


def ecb_xml(day: date, usd: str = "1.1000") -> str:
    return (f"<Cube><Cube time='{day.isoformat()}'><Cube currency='USD' rate='{usd}'/><Cube currency='JPY' rate='160.00'/>"
            "<Cube currency='GBP' rate='0.8500'/><Cube currency='INR' rate='92.000'/><Cube currency='CHF' rate='0.9500'/></Cube></Cube>")


@pytest.fixture
def ecb(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The ECB file served by a mock transport (the real connector, size cap and strict parser run)."""
    state: dict[str, Any] = {"status": 200, "body": ecb_xml(today()), "calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        assert str(request.url) == "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
        return httpx.Response(state["status"], text=state["body"])

    monkeypatch.setattr(connectors, "finance_transport", httpx.MockTransport(handler))
    return state


def feed(headers: dict = OWNER, **body: Any) -> dict:
    current = client.get(f"{API}/rates/auto", headers=headers).json()
    response = client.put(f"{API}/rates/auto", headers=headers, json={"enabled": True, "currencies": ["USD", "GBP"], "version": current["version"], **body})
    assert response.status_code == 200, response.text
    return response.json()


def rates(headers: dict = OWNER, **params: Any) -> list[dict]:
    return client.get(f"{API}/rates", headers=headers, params={"limit": 200, **params}).json()["items"]


def test_the_daily_job_stores_ecb_rates_with_their_date_and_source_and_never_invents_one(ecb: dict) -> None:
    setup_books(base_currency="AED", country="AE")
    assert client.get(f"{API}/rates/auto", headers=OWNER).json()["enabled"] is False
    data = feed(currencies=["USD", "GBP", "SAR", "KWD"])
    assert data["followed"] == ["AED", "GBP", "KWD", "SAR", "USD"]
    result = books_jobs.run()
    assert result["feeds"] == 1 and result["rates_added"] == 3 and ecb["calls"] == 1
    stored = {(r["base"], r["quote"]): r for r in rates(origin="ecb")}
    assert set(stored) == {("EUR", "USD"), ("EUR", "GBP"), ("EUR", "AED")}
    assert stored[("EUR", "USD")]["source"] == "ECB" and stored[("EUR", "USD")]["as_of"] == today().isoformat()
    assert stored[("EUR", "AED")]["rate"] == "4.03975" and "CBUAE peg 3.6725" in stored[("EUR", "AED")]["source"]
    status = client.get(f"{API}/rates/auto", headers=MEMBER).json()
    assert status["last_as_of"] == today().isoformat() and status["last_error"] == "" and status["unavailable"] == ["KWD", "SAR"]
    # Running again the same day adds nothing; the report converts with them and names the source.
    assert books_jobs.run()["rates_added"] == 0
    assert bill(vendor()["id"], subtotal="110", tax="0", currency="USD").status_code == 201
    data = report()
    assert data["pnl"]["operating_expenses"]["total"] == "403.98"  # 110 USD = 100 EUR = 403.975 AED, rounded half to even once
    assert {c["source"] for c in data["conversions"]} == {"ECB; ECB, AED from EUR/USD at the CBUAE peg 3.6725 AED per USD"}


def test_a_rate_an_owner_types_always_wins_over_a_fetched_one_for_the_same_day(ecb: dict) -> None:
    setup_books(base_currency="EUR", country="AE")
    feed(currencies=["USD"])
    typed = client.post(f"{API}/rates", headers=OWNER, json={"base": "EUR", "quote": "USD", "rate": "1.2", "as_of": today().isoformat(),
                                                             "source": "Synthetic bank rate"})
    assert typed.status_code == 201
    assert books_jobs.run()["rates_added"] == 0  # the typed rate for that pair and day stays; nothing fetched over it
    assert [(r["rate"], r["origin"]) for r in rates()] == [("1.2", "manual")]
    client.delete(f"{API}/rates/{typed.json()['id']}", headers=OWNER)
    assert books_jobs.run()["rates_added"] == 1
    assert [(r["rate"], r["origin"]) for r in rates()] == [("1.1", "ecb")]
    # An owner's rate for a pair and day that has a fetched one replaces it.
    again = client.post(f"{API}/rates", headers=OWNER, json={"base": "EUR", "quote": "USD", "rate": "1.15", "as_of": today().isoformat(),
                                                             "source": "Synthetic bank rate"})
    assert again.status_code == 201, again.text
    assert [(r["rate"], r["origin"]) for r in rates()] == [("1.15", "manual")]
    assert bill(vendor()["id"], subtotal="115", tax="0", currency="USD").status_code == 201
    assert report()["pnl"]["operating_expenses"]["total"] == "100.00"


def test_the_converter_prefers_a_typed_inverse_rate_on_the_same_day() -> None:
    now, day = utc_now(), today()

    def rate(base: str, quote: str, value: str, origin: RateOrigin) -> Rate:
        return Rate(id=f"r_{base}{quote}{origin}", tenant_id="t", workspace_id="w", created_at=now, base=base, quote=quote, rate=Decimal(value),
                    as_of=day, source="Synthetic", created_by="u", origin=origin)

    converter = Converter([rate("SAR", "USD", "0.30", RateOrigin.ECB), rate("USD", "SAR", "3.75", RateOrigin.MANUAL)], "SAR", 31)
    assert converter.convert(Decimal("100"), "USD", day) == Decimal("375.00")


def test_a_failed_fetch_keeps_the_last_rates_and_says_when_they_were_updated(ecb: dict) -> None:
    setup_books(base_currency="EUR")
    feed(currencies=["USD"])
    books_jobs.run()
    ecb["status"] = 503
    result = books_jobs.run(now=utc_now() + timedelta(days=1))
    assert result["fetch_failed"] is True and result["rates_added"] == 0
    status = client.get(f"{API}/rates/auto", headers=OWNER).json()
    assert "503" in status["last_error"] and status["last_as_of"] == today().isoformat() and status["last_success_at"] is not None
    assert len(rates(origin="ecb")) == 1
    # A file that is not the reference rates is refused the same way.
    ecb["status"], ecb["body"] = 200, "<html>not rates</html>"
    books_jobs.run(now=utc_now() + timedelta(days=2))
    assert client.get(f"{API}/rates/auto", headers=OWNER).json()["last_error"] == "The ECB answer has no single rates date."
    assert len(rates(origin="ecb")) == 1


def test_only_owners_turn_rates_on_and_fetch_now_waits_ten_minutes(ecb: dict) -> None:
    setup_books(base_currency="EUR")
    refused = client.put(f"{API}/rates/auto", headers=MEMBER, json={"enabled": True, "currencies": ["USD"], "version": None})
    assert refused.status_code == 403
    assert client.post(f"{API}/rates/auto/refresh", headers=OWNER).json()["code"] == "feed_off"
    feed(currencies=["USD"])
    stale = client.put(f"{API}/rates/auto", headers=OWNER, json={"enabled": False, "currencies": [], "version": None})
    assert stale.status_code == 409 and stale.json()["code"] == "stale"
    bad = client.put(f"{API}/rates/auto", headers=OWNER, json={"enabled": True, "currencies": ["usdollar"], "version": 1})
    assert bad.status_code == 422
    now = client.post(f"{API}/rates/auto/refresh", headers=OWNER)
    assert now.status_code == 200 and now.json()["added"] == 1
    assert client.post(f"{API}/rates/auto/refresh", headers=OWNER).status_code == 429
    assert client.post(f"{API}/rates/auto/refresh", headers=MEMBER).status_code == 403
    off = feed(enabled=False)
    assert off["enabled"] is False and books_jobs.run()["feeds"] == 0


def test_no_fetch_happens_when_no_business_opted_in(ecb: dict) -> None:
    setup_books()
    # The posting run reads this business's books (nothing new to post: settings were saved and posted already).
    assert books_jobs.run() == {"dry_run": False, "feeds": 0, "rates_added": 0, "fetch_failed": False, "businesses": 0, "bills_made": 0, "failed": 0,
                                "ledgers": 1, "entries_posted": 0, "entries_reversed": 0, "not_posted": 0}
    assert ecb["calls"] == 0


# -- recurring bills ----------------------------------------------------------------------------------------------------


def test_the_schedule_keeps_its_day_and_steps_by_days_weeks_and_months() -> None:
    jan31 = date(2027, 1, 31)
    assert [schedule.occurrence(jan31, Frequency.MONTHLY, 1, k) for k in range(4)] == [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30)]
    assert schedule.occurrence(date(2028, 1, 31), Frequency.MONTHS, 1, 1) == date(2028, 2, 29)
    assert [schedule.occurrence(jan31, Frequency.QUARTERLY, 1, k) for k in range(3)] == [date(2027, 1, 31), date(2027, 4, 30), date(2027, 7, 31)]
    assert schedule.occurrence(date(2028, 2, 29), Frequency.YEARLY, 1, 1) == date(2029, 2, 28)
    assert schedule.occurrence(jan31, Frequency.WEEKS, 2, 3) == date(2027, 3, 14)
    assert schedule.occurrence(jan31, Frequency.DAYS, 10, 2) == date(2027, 2, 20)
    assert schedule.first_index_on_or_after(jan31, Frequency.MONTHLY, 1, date(2027, 3, 1)) == 2
    assert schedule.first_index_on_or_after(jan31, Frequency.WEEKS, 1, date(2027, 2, 7)) == 1
    assert schedule.first_index_on_or_after(jan31, Frequency.DAYS, 3, date(2027, 1, 31)) == 0


def make_template(headers: dict = OWNER, **body: Any) -> dict:
    payload = {"vendor_id": body.pop("vendor_id", None) or vendor()["id"], "description": "Synthetic office rent", "currency": "SAR", "subtotal": "1000",
               "tax": "150", "category": "rent", "frequency": "monthly", "start_date": today().isoformat(), "due_days": 10, **body}
    response = client.post(f"{API}/recurring", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_a_recurring_bill_makes_each_due_bill_once_as_a_normal_bill() -> None:
    setup_books()
    template = make_template(max_count=3)
    assert template["created_count"] == 1 and len(template["bills"]) == 1
    first = template["bills"][0]
    assert (first["bill_date"], first["due_date"], first["total"], first["status"]) == (today().isoformat(), (today() + timedelta(days=10)).isoformat(),
                                                                                        "1150.00", "open")
    assert first["template_id"] == template["id"] and first["period_date"] == today().isoformat()
    # The job the same day, twice, makes nothing more; a month later it makes the next one, and the third ends the schedule.
    assert books_jobs.run()["bills_made"] == 0 and books_jobs.run()["bills_made"] == 0
    later = utc_now() + timedelta(days=95)
    assert books_jobs.run(now=later)["bills_made"] == 2
    detail = client.get(f"{API}/recurring/{template['id']}", headers=MEMBER).json()
    assert detail["status"] == "ended" and detail["end_reason"] == "count" and detail["created_count"] == 3 and detail["next_date"] is None
    assert len({b["period_date"] for b in detail["bills"]}) == 3
    assert books_jobs.run(now=later + timedelta(days=60))["bills_made"] == 0
    # They are normal bills: listed, paid and counted like any other.
    listed = client.get(f"{API}/bills", headers=OWNER, params={"status": "recurring"}).json()
    assert listed["total"] == 3
    paid = client.post(f"{API}/bills/{first['id']}/payments", headers=OWNER, json={"amount": "1150", "method": "bank_transfer"})
    assert paid.status_code == 201 and paid.json()["payment_state"] == "paid"
    assert report()["pnl"]["operating_expenses"]["total"] >= "1000.00"


def test_two_runs_at_once_make_one_bill_per_period() -> None:
    from anum_api.business.books_recurring import materialize
    from anum_api.business.books_store import open_books_store
    from anum_api.schemas import TenantContext

    setup_books()
    template = make_template(start_date=(today() - timedelta(days=62)).isoformat(), frequency="months", interval=1)
    assert template["created_count"] == 3
    context = TenantContext(tenant_id=TENANT, workspace_id=BUSINESS, user_id="system:books")
    stored = books_memory_store.rows["templates"][template["id"]]
    # A run that read the template before the first one saved it: the periods it tries already exist, so nothing is made twice.
    books_memory_store.rows["templates"][template["id"]] = stored.model_copy(update={"next_index": 0, "next_date": date.fromisoformat(template["start_date"]),
                                                                                       "created_count": 0})
    with open_books_store(context) as store:
        _, made = materialize(store, template["id"], today=today(), now=utc_now())
    assert made == []
    assert client.get(f"{API}/bills", headers=OWNER).json()["total"] == 3


def test_draft_bills_count_nowhere_until_approved_and_cannot_be_paid() -> None:
    setup_books()
    template = make_template(create_as="draft", tax="0")
    draft = template["bills"][0]
    assert draft["status"] == "draft" and draft["payment_state"] == "draft" and draft["can"]["approve"] is True and draft["can"]["pay"] is False
    assert report()["pnl"]["operating_expenses"]["total"] == "0.00" and report()["balances"]["payables"] == []
    refused = client.post(f"{API}/bills/{draft['id']}/payments", headers=OWNER, json={"amount": "10", "method": "cash"})
    assert refused.status_code == 409 and refused.json()["code"] == "bill_draft"
    assert client.get(f"{API}/bills", headers=OWNER, params={"status": "draft"}).json()["total"] == 1
    assert client.post(f"{API}/bills/{draft['id']}/approve", headers=VIEWER, json={"version": draft["version"]}).status_code == 403
    # Maker and checker (defect B8): a member who is not an accountant cannot approve, nor can the owner who made the recurring bill.
    member = client.post(f"{API}/bills/{draft['id']}/approve", headers=MEMBER, json={"version": draft["version"]})
    assert member.status_code == 403 and member.json()["code"] == "forbidden"
    own = client.post(f"{API}/bills/{draft['id']}/approve", headers=OWNER, json={"version": draft["version"]})
    assert own.status_code == 403 and own.json()["code"] == "own_draft"
    approved = client.post(f"{API}/bills/{draft['id']}/approve", headers=OWNER_TWO, json={"version": draft["version"]})
    assert approved.status_code == 200 and approved.json()["status"] == "open" and approved.json()["payment_state"] == "unpaid"
    assert report()["pnl"]["operating_expenses"]["total"] == "1000.00"
    again = client.post(f"{API}/bills/{draft['id']}/approve", headers=OWNER_TWO, json={"version": approved.json()["version"]})
    assert again.status_code == 409 and again.json()["code"] == "not_draft"


def test_pause_skips_the_periods_it_missed_edit_changes_only_future_bills_and_end_is_final() -> None:
    setup_books()
    template = make_template(frequency="weeks", interval=1)
    paused = client.post(f"{API}/recurring/{template['id']}/pause", headers=MEMBER, json={"version": template["version"]})
    assert paused.status_code == 200 and paused.json()["status"] == "paused" and paused.json()["upcoming"] == []
    assert books_jobs.run(now=utc_now() + timedelta(days=21))["bills_made"] == 0
    stale = client.post(f"{API}/recurring/{template['id']}/resume", headers=MEMBER, json={"version": template["version"]})
    assert stale.status_code == 409 and stale.json()["code"] == "stale"
    resumed = client.post(f"{API}/recurring/{template['id']}/resume", headers=MEMBER, json={"version": paused.json()["version"]})
    assert resumed.json()["status"] == "active" and resumed.json()["next_date"] >= today().isoformat()
    # The amount changes for the bills made from now on; the bill already made keeps its figures.
    body = {key: resumed.json()[key] for key in ("vendor_id", "description", "currency", "category", "frequency", "interval", "start_date", "due_days")}
    edited = client.patch(f"{API}/recurring/{template['id']}", headers=MEMBER, json={**body, "subtotal": "1200", "tax": "0", "version": resumed.json()["version"]})
    assert edited.status_code == 200, edited.text
    books_jobs.run(now=utc_now() + timedelta(days=7))
    bills = client.get(f"{API}/recurring/{template['id']}", headers=MEMBER).json()["bills"]
    assert sorted(b["total"] for b in bills) == ["1150.00", "1200.00"]
    # A new schedule starts after the last bill made; a past start is refused.
    moved = client.patch(f"{API}/recurring/{template['id']}", headers=MEMBER, json={**body, "subtotal": "1200", "tax": "0", "frequency": "monthly",
                                                                                    "version": edited.json()["version"] + 1})
    assert moved.status_code == 422 and moved.json()["code"] == "start_before_last_bill"
    current = client.get(f"{API}/recurring/{template['id']}", headers=MEMBER).json()
    ended = client.post(f"{API}/recurring/{template['id']}/end", headers=OWNER, json={"version": current["version"]})
    assert ended.json()["status"] == "ended" and ended.json()["end_reason"] == "owner"
    assert client.patch(f"{API}/recurring/{template['id']}", headers=MEMBER, json={**body, "subtotal": "1", "tax": "0", "version": ended.json()["version"]}
                        ).json()["code"] == "template_ended"
    assert client.post(f"{API}/recurring/{template['id']}/resume", headers=MEMBER, json={"version": ended.json()["version"]}).json()["code"] == "template_ended"
    assert books_jobs.run(now=utc_now() + timedelta(days=60))["bills_made"] == 0


def test_recurring_bills_are_validated_like_bills_and_viewers_cannot_make_them() -> None:
    setup_books()
    landlord = vendor()
    base = {"vendor_id": landlord["id"], "description": "Synthetic rent", "currency": "SAR", "subtotal": "100", "category": "rent", "frequency": "monthly",
            "start_date": today().isoformat()}
    cases = [({"subtotal": "0", "tax": "0"}, "invalid_amount"), ({"frequency": "days", "interval": 0}, None),
             ({"frequency": "weeks", "interval": 60}, "invalid_interval"), ({"end_date": (today() - timedelta(days=1)).isoformat()}, "invalid_end_date"),
             ({"start_date": (today() - timedelta(days=400)).isoformat()}, "start_too_old"), ({"vendor_id": "bven_missing"}, "vendor_not_found"),
             ({"useful_life_months": 12}, "life_needs_capital")]
    for change, code in cases:
        response = client.post(f"{API}/recurring", headers=OWNER, json={**base, **change})
        assert response.status_code in (404, 422), (change, response.text)
        if code:
            assert response.json()["code"] == code, (change, response.text)
    assert client.post(f"{API}/recurring", headers=VIEWER, json=base).status_code == 403
    assert client.get(f"{API}/recurring", headers=VIEWER).status_code == 403
    assert client.get(f"{API}/recurring", headers=OTHER_OWNER).json()["total"] == 0


def test_the_recurring_costs_report_shows_each_recurring_bill_and_the_bills_coming() -> None:
    setup_books()
    landlord = vendor()
    future = today() + timedelta(days=5)
    rent = make_template(vendor_id=landlord["id"], start_date=future.isoformat(), tax="0")
    make_template(vendor_id=landlord["id"], category="cleaning", subtotal="70", tax="0", frequency="weeks", interval=2, start_date=future.isoformat())
    data = report()
    items = {item["category"]: item for item in data["recurring"]["items"]}
    assert items["rent"]["per_month"] == "1000.00" and items["rent"]["next_date"] == future.isoformat()
    assert items["cleaning"]["per_month"] == "152.19"  # 70 every 14 days = 70 x 30.4375 / 14
    assert data["recurring"]["per_month"] == [{"currency": "SAR", "amount": "1152.19"}]
    upcoming = data["recurring"]["upcoming"]
    assert upcoming[0]["bill_date"] == future.isoformat() and upcoming[0]["due_date"] == (future + timedelta(days=10)).isoformat()
    assert len([u for u in upcoming if u["template_id"] == rent["id"]]) == 3  # monthly, within 90 days
    listed = client.get(f"{API}/recurring", headers=MEMBER).json()
    assert listed["total"] == 2 and listed["upcoming"] == upcoming[:30]


def test_the_daily_job_reports_counts_only_and_a_dry_run_changes_nothing(ecb: dict) -> None:
    setup_books()
    make_template(start_date=(today() + timedelta(days=1)).isoformat())
    feed(currencies=["USD"])
    dry = books_jobs.run(dry_run=True, now=utc_now() + timedelta(days=2))
    assert dry["dry_run"] is True and dry["bills_made"] == 0 and ecb["calls"] == 0
    assert client.get(f"{API}/bills", headers=OWNER).json()["total"] == 0
    assert set(dry) == {"dry_run", "feeds", "rates_added", "fetch_failed", "businesses", "bills_made", "failed", "ledgers", "entries_posted",
                        "entries_reversed", "not_posted"}
    assert client.get(f"{API}/ledger/journal", headers=OWNER).json()["total"] == 0  # the dry run posted nothing either


def test_templates_in_memory_follow_the_status_rules() -> None:
    # The status enum the job and the API compare against.
    assert {item.value for item in TemplateStatus} == {"active", "paused", "ended"}
    assert BillTemplate.model_fields["create_as"].default == "open"
    assert books_rates_auto.followed(RateFeed(id="f", tenant_id="t", workspace_id="w", created_at=utc_now(), currencies=["EUR", "usd", "USD"],
                                                               updated_by="u", updated_at=utc_now()), "AED") == ["AED", "USD"]


# -- review fixes ---------------------------------------------------------------------------------------------------------


def test_the_job_reaches_a_due_recurring_bill_behind_more_than_500_that_are_not_due() -> None:
    setup_books()
    landlord = vendor()
    later = today() + timedelta(days=200)
    future = make_template(vendor_id=landlord["id"], start_date=later.isoformat())
    stored = books_memory_store.rows["templates"][future["id"]]
    old = utc_now() - timedelta(days=30)
    for index in range(520):  # older and not due: a first page of 500 never reaches the due one
        clone = stored.model_copy(update={"id": f"btpl_wait_{index:04d}", "created_at": old + timedelta(seconds=index)})
        books_memory_store.rows["templates"][clone.id] = clone
    due = make_template(vendor_id=landlord["id"], start_date=(today() + timedelta(days=1)).isoformat())
    result = books_jobs.run(now=utc_now() + timedelta(days=1))
    assert result["bills_made"] == 1 and result["failed"] == 0, result
    assert client.get(f"{API}/recurring/{due['id']}", headers=OWNER).json()["created_count"] == 1


def test_more_businesses_than_one_view_holds_are_listed_as_excluded_never_dropped_silently() -> None:
    from anum_api.business.api_books_more import MAX_GROUP

    setup_books()
    now = utc_now()
    extra = [f"ws_books_extra_{index:02d}" for index in range(MAX_GROUP + 1)]
    for workspace in extra:
        memory_repository.store.memberships[(TENANT, workspace, "owner_books")] = WorkspaceMembership(
            tenant_id=TENANT, workspace_id=workspace, user_id="owner_books", role="owner", active=True, created_at=now, updated_at=now)
        setup_books({**OWNER, "x-workspace-id": workspace})
    data = group()
    included = [b["workspace_id"] for b in data["businesses"]]
    excluded = [b["workspace_id"] for b in data["excluded"]]
    assert len(included) == MAX_GROUP and BUSINESS in included
    # Every business with books is either in the view or named as left out; ones without books are skipped, never counted.
    assert sorted(included + excluded) == sorted([BUSINESS, *extra]) and all(b["reason"] == "limit" for b in data["excluded"])
    assert {item["workspace_id"] for item in data["available"]} == {BUSINESS, *extra}
    assert PERSONAL not in included + excluded and any(item["workspace_id"] == PERSONAL for item in data["skipped"])
    rows = list(csv.reader(io.StringIO(client.get(f"{API}/group.csv", headers=OWNER).text)))
    assert sorted(r[1] for r in rows if r[0] == "excluded") == sorted(excluded)
    too_many = client.get(f"{API}/group", headers=OWNER, params={"workspace": [BUSINESS, *extra]})
    assert too_many.status_code == 422 and too_many.json()["code"] == "too_many_businesses"
