"""Personal finance, Stage 2 (docs/finance.md): ledger, CSV import, summary, tools, skill routing.

In-memory stores; ``test_postgres_finance.py`` repeats the isolation checks under RLS.
Fixtures are plainly fake (merchant words, round amounts).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.agent_planning import choose_tool_call
from anum_api.agent_skills import default_skill_registry
from anum_api.agent_tools import ToolCall
from anum_api.authorization import ROLE_PERMISSIONS, Permission, Role
from anum_api.chat_tools import chat_intent
from anum_api.finance import csv_import
from anum_api.finance.categories import categorize
from anum_api.finance.models import Category, CategoryRule, CategorySource
from anum_api.finance.money import MoneyError, describe, quantize, to_money, totals
from anum_api.finance.store import finance_store
from anum_api.finance.summary import add_months, build_summary
from anum_api.finance.tools import is_money_movement, plan_call
from anum_api.schemas import TenantContext

TENANT, WORKSPACE = "tenant_fin", "workspace_fin"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_fin", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_fin", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_fin", "x-user-roles": "viewer"}
OTHER_WORKSPACE = {**OWNER, "x-workspace-id": "workspace_elsewhere"}
API = "/api/v1/finance"

client = TestClient(main.app)

ENBD = (
    "Account statement,example\n"
    "Transaction Date,Narration,Debit,Credit\n"
    "01/10/2026,CARREFOUR CITY CENTRE,120.50,\n"
    "02/10/2026,SALARY OCTOBER,,15000.00\n"
    "03/10/2026,DEWA UTILITY,450.00,\n"
    "03/10/2026,TALABAT ORDER,35.00,\n"
    "03/10/2026,TALABAT ORDER,35.00,\n"
    "04/10/2026,=HYPERLINK(example),10.00,\n"
    "not a date,BROKEN ROW,1.00,\n"
)
GENERIC = "Date,Description,Amount\n2026-10-05,SPINNEYS,-80.25\n2026-10-06,REFUND SHOP,20\n"


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    yield
    finance_store.clear()


def account(headers: dict = OWNER, **body: object) -> dict:
    response = client.post(f"{API}/accounts", headers=headers, json={"name": "Current", "kind": "bank", **body})
    assert response.status_code == 201, response.text
    return response.json()


def do_import(account_id: str, content: str = ENBD, headers: dict = OWNER, **extra: object) -> dict:
    response = client.post(f"{API}/imports", headers=headers, json={"account_id": account_id, "content": content, "filename": "oct.csv", **extra})
    assert response.status_code == 201, response.text
    return response.json()


# -- money ----------------------------------------------------------------------------------


def test_money_rounds_once_half_to_even_to_the_currency_minor_unit() -> None:
    assert quantize(Decimal("0.125"), "AED") == Decimal("0.12")
    assert quantize(Decimal("0.135"), "AED") == Decimal("0.14")
    assert quantize(Decimal("1.0005"), "KWD") == Decimal("1.000")
    assert quantize(Decimal("12.5"), "JPY") == Decimal("12")
    assert to_money("1000.555", "AED") == Decimal("1000.56")
    assert to_money(0.1, "AED") == Decimal("0.10")
    with pytest.raises(MoneyError):
        to_money("ten", "AED")
    with pytest.raises(MoneyError):
        to_money("1e20", "AED")


def test_totals_never_add_different_currencies() -> None:
    sums = totals([("AED", Decimal("10")), ("USD", Decimal("5")), ("AED", Decimal("2.5"))])
    assert sums == {"AED": Decimal("12.5"), "USD": Decimal("5")}
    assert describe(sums) == "AED 12.50 and USD 5.00"


# -- categories -----------------------------------------------------------------------------


def test_rule_based_categories_with_owner_rules_first() -> None:
    assert categorize("CARREFOUR MOE", Decimal("-10")) == (Category.GROCERIES, CategorySource.RULE)
    assert categorize("DU TELECOM", Decimal("-10"))[0] == Category.TELECOM
    assert categorize("dubai mall parking", Decimal("-10"))[0] != Category.TELECOM  # "du" is a whole word only
    assert categorize("SALARY", Decimal("100"))[0] == Category.INCOME
    assert categorize("UNKNOWN SHOP", Decimal("-10")) == (Category.OTHER, CategorySource.NONE)
    rule = CategoryRule(match="unknown shop", category=Category.SHOPPING)
    assert categorize("UNKNOWN SHOP", Decimal("-10"), [rule]) == (Category.SHOPPING, CategorySource.OWNER_RULE)


# -- CSV parsing ----------------------------------------------------------------------------


def test_bank_preset_is_detected_after_a_preamble_and_rows_are_read() -> None:
    parsed = csv_import.parse_file(ENBD, account_id="a", currency="AED")
    assert parsed.preset == "emirates_nbd"
    assert parsed.mapping.debit == "Debit" and parsed.mapping.credit == "Credit"
    assert parsed.date_format == "%d/%m/%Y" and parsed.date_format_ambiguous is True
    assert [r.amount for r in parsed.rows[:3]] == [Decimal("-120.50"), Decimal("15000.00"), Decimal("-450.00")]
    assert len(parsed.errors) == 1 and parsed.errors[0].line == 9
    # Two identical rows in one file are two transactions (occurrence in the hash).
    talabat = [r.dedupe_hash for r in parsed.rows if r.description == "TALABAT ORDER"]
    assert len(set(talabat)) == 2


def test_generic_amount_column_and_date_format_override() -> None:
    parsed = csv_import.parse_file(GENERIC, account_id="a", currency="AED")
    assert parsed.preset == "generic" and parsed.mapping.amount == "Amount"
    assert parsed.rows[0].posted_on == date(2026, 10, 5) and parsed.rows[0].amount == Decimal("-80.25")
    us = "Date,Description,Amount\n10/02/2026,SHOP,-1\n"
    assert csv_import.parse_file(us, account_id="a", currency="AED").rows[0].posted_on == date(2026, 2, 10)
    assert csv_import.parse_file(us, account_id="a", currency="AED", date_format="%m/%d/%Y").rows[0].posted_on == date(2026, 10, 2)


def test_custom_mapping_and_amount_formats() -> None:
    text = "When;What;Money\n05.10.2026;KIOSK;(12,50)\n06.10.2026;KIOSK;1.234,00 CR\n"
    mapping = csv_import.ColumnMapping(date="When", description="What", amount="Money")
    parsed = csv_import.parse_file(text, account_id="a", currency="AED", mapping=mapping)
    assert parsed.preset == "custom" and [r.amount for r in parsed.rows] == [Decimal("-12.50"), Decimal("1234.00")]
    with pytest.raises(csv_import.ImportError_):
        csv_import.parse_file("a,b\n1,2\n", account_id="a", currency="AED")


def test_untrusted_files_are_capped_and_cleaned() -> None:
    with pytest.raises(csv_import.ImportError_):
        csv_import.decode(b"x" * (csv_import.MAX_BYTES + 1))
    rows = "Date,Description,Amount\n" + "2026-10-01,X,-1\n" * (csv_import.MAX_ROWS + 1)
    with pytest.raises(csv_import.ImportError_):
        csv_import.parse_file(rows, account_id="a", currency="AED")
    assert csv_import.clean_text("SHOP‮\u0000 NAME") == "SHOP NAME"
    assert csv_import.safe_cell("=1+1") == "'=1+1" and csv_import.safe_cell("@SUM") == "'@SUM" and csv_import.safe_cell("Shop") == "Shop"


# -- API ------------------------------------------------------------------------------------


def test_permissions_owner_and_member_have_finance_viewer_has_none() -> None:
    for role in (Role.OWNER, Role.MEMBER):
        assert {Permission.FINANCE_READ, Permission.FINANCE_MANAGE} <= ROLE_PERMISSIONS[role]
    assert Permission.FINANCE_READ not in ROLE_PERMISSIONS[Role.VIEWER]
    denied = client.get(f"{API}/summary", headers=VIEWER)
    assert denied.status_code == 403 and "owners and members" in denied.json()["error"]["message"]
    assert client.post(f"{API}/accounts", headers=VIEWER, json={"name": "X", "kind": "cash"}).status_code == 403
    assert client.get(f"{API}/summary", headers=MEMBER).status_code == 200


def test_account_crud_defaults_to_aed_and_keeps_only_four_digits() -> None:
    created = account(last4="1234", opening_balance="1000")
    assert created["currency"] == "AED" and created["balance"] == "1000.00" and created["last4"] == "1234"
    assert client.post(f"{API}/accounts", headers=OWNER, json={"name": "X", "kind": "card", "last4": "4111111111111111"}).status_code == 422
    patched = client.patch(f"{API}/accounts/{created['id']}", headers=OWNER, json={"name": "Salary account", "archived": True})
    assert patched.status_code == 200 and patched.json()["name"] == "Salary account" and patched.json()["archived"] is True
    assert client.get(f"{API}/accounts/{created['id']}", headers=OWNER).status_code == 200
    assert client.delete(f"{API}/accounts/{created['id']}", headers=OWNER).status_code == 204
    assert client.get(f"{API}/accounts/{created['id']}", headers=OWNER).status_code == 404


def test_ledger_is_private_to_its_owner_even_from_workspace_owners() -> None:
    mine = account(MEMBER, name="Member's")
    assert client.get(f"{API}/accounts", headers=OWNER).json() == []
    assert client.get(f"{API}/accounts/{mine['id']}", headers=OWNER).status_code == 404
    assert client.delete(f"{API}/accounts/{mine['id']}", headers=OWNER).status_code == 404
    assert client.get(f"{API}/accounts", headers={**MEMBER, "x-workspace-id": "workspace_elsewhere"}).json() == []
    assert [a["id"] for a in client.get(f"{API}/accounts", headers=MEMBER).json()] == [mine["id"]]


def test_import_preview_then_confirm_then_reimport_adds_nothing() -> None:
    acct = account()
    preview = client.post(f"{API}/imports/preview", headers=OWNER, json={"account_id": acct["id"], "content": ENBD})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert (body["preset"], body["valid_count"], body["error_count"], body["new_count"]) == ("emirates_nbd", 6, 1, 6)
    assert body["rows"][0]["category"] == "groceries" and body["rows"][0]["amount"] == "-120.50"
    assert finance_store.transactions == {}  # a preview saves nothing
    first = do_import(acct["id"])
    assert (first["imported_count"], first["duplicate_count"], first["error_count"]) == (6, 0, 1)
    again = do_import(acct["id"])
    assert (again["imported_count"], again["duplicate_count"]) == (0, 6)
    assert client.post(f"{API}/imports/preview", headers=OWNER, json={"account_id": acct["id"], "content": ENBD}).json()["duplicate_count"] == 6
    listed = client.get(f"{API}/imports", headers=OWNER).json()
    assert listed["total"] == 2
    # Undo the first import: its rows go.
    assert client.delete(f"{API}/imports/{first['id']}", headers=OWNER).status_code == 204
    assert client.get(f"{API}/transactions", headers=OWNER).json()["total"] == 0


def test_import_errors_say_what_to_fix() -> None:
    acct = account()
    bad = client.post(f"{API}/imports/preview", headers=OWNER, json={"account_id": acct["id"], "content": "a,b\n1,2\n"})
    assert bad.status_code == 422 and "Column mapping" in bad.json()["error"]["message"]
    missing = client.post(f"{API}/imports/preview", headers=OWNER, json={"account_id": "fin_acct_nope", "content": GENERIC})
    assert missing.status_code == 404
    assert client.post(f"{API}/imports/preview", headers=OWNER, json={"account_id": acct["id"], "content": GENERIC, "preset": "nope"}).status_code == 422


def test_transactions_search_paging_edit_delete_and_safe_export() -> None:
    acct = account()
    do_import(acct["id"])
    page = client.get(f"{API}/transactions", headers=OWNER, params={"limit": 2}).json()
    assert page["total"] == 6 and len(page["items"]) == 2 and page["items"][0]["posted_on"] == "2026-10-04"
    found = client.get(f"{API}/transactions", headers=OWNER, params={"q": "talabat"}).json()
    assert found["total"] == 2
    assert client.get(f"{API}/transactions", headers=OWNER, params={"category": "income"}).json()["total"] == 1
    assert client.get(f"{API}/transactions", headers=OWNER, params={"direction": "in"}).json()["total"] == 1
    assert client.get(f"{API}/transactions", headers=OWNER, params={"from": "2026-10-03", "to": "2026-10-03"}).json()["total"] == 3
    target = found["items"][0]["id"]
    edited = client.patch(f"{API}/transactions/{target}", headers=OWNER, json={"category": "entertainment", "notes": "Friday"})
    assert edited.json()["category"] == "entertainment" and edited.json()["category_source"] == "manual"
    manual = client.post(
        f"{API}/transactions", headers=OWNER,
        json={"account_id": acct["id"], "posted_on": "2026-10-07", "amount": "-15.005", "description": "ENOC STATION"},
    )
    assert manual.status_code == 201 and manual.json()["amount"] == "-15.00" and manual.json()["category"] == "fuel"
    assert client.delete(f"{API}/transactions/{target}", headers=OWNER).status_code == 204
    export = client.get(f"{API}/transactions/export.csv", headers=OWNER)
    assert export.status_code == 200 and export.headers["content-type"].startswith("text/csv")
    assert "'=HYPERLINK(example)" in export.text and "\n=HYPERLINK" not in export.text


def test_summary_is_per_currency_with_spend_income_bills_and_debts() -> None:
    aed = account(opening_balance="1000")
    usd = account(name="Dollar", currency="USD")
    do_import(aed["id"])
    do_import(usd["id"], GENERIC)
    client.post(f"{API}/bills", headers=OWNER, json={"name": "DEWA", "amount": "450", "next_due_on": "2026-10-20"})
    client.post(f"{API}/debts", headers=OWNER, json={"name": "Card", "kind": "card", "balance": "5000", "minimum_payment": "250"})
    client.post(f"{API}/goals", headers=OWNER, json={"name": "Emergency fund", "target_amount": "10000", "saved_amount": "2500"})
    now = datetime(2026, 10, 14, 8, tzinfo=timezone.utc)
    from anum_api.finance.store import open_finance_store

    context = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_fin")
    with open_finance_store(context) as store:
        data = build_summary(store, context, None, now=now)
    assert data.month == "2026-10" and data.base_currency == "AED" and data.timezone == "Asia/Dubai"
    assert {a.currency: a.amount for a in data.spend} == {"AED": Decimal("650.50"), "USD": Decimal("80.25")}
    assert {a.currency: a.amount for a in data.income} == {"AED": Decimal("15000.00"), "USD": Decimal("20.00")}
    assert {a.currency: a.amount for a in data.balances} == {"AED": Decimal("15349.50"), "USD": Decimal("-60.25")}
    assert data.spend_by_category[0].category == Category.UTILITIES
    assert [b.days_until for b in data.upcoming_bills] == [6]
    assert {a.currency: a.amount for a in data.debts} == {"AED": Decimal("5000.00")} and data.goals[0].percent == 25
    over_http = client.get(f"{API}/summary", headers=OWNER, params={"month": "2026-10"}).json()
    assert over_http["spend"] == [{"currency": "AED", "amount": "650.50"}, {"currency": "USD", "amount": "80.25"}]
    assert client.get(f"{API}/summary", headers=OWNER, params={"month": "2026-13"}).status_code == 422


def test_bills_debts_goals_holdings_crud_and_rules() -> None:
    bill = client.post(f"{API}/bills", headers=OWNER, json={"name": "Rent", "amount": "6000", "cadence": "monthly", "next_due_on": "2026-01-31", "category": "housing"}).json()
    upcoming = client.get(f"{API}/bills/upcoming", headers=OWNER, params={"days": 366}).json()
    assert upcoming and all(row["name"] == "Rent" for row in upcoming)
    assert client.patch(f"{API}/bills/{bill['id']}", headers=OWNER, json={"name": "Rent", "amount": "6500", "next_due_on": "2026-11-01"}).json()["amount"] == "6500.00"
    assert client.post(f"{API}/bills", headers=OWNER, json={"name": "X", "amount": "-1", "next_due_on": "2026-11-01"}).status_code == 422
    debt = client.post(f"{API}/debts", headers=OWNER, json={"name": "Car loan", "balance": "40000", "apr_percent": "4.5"}).json()
    assert debt["apr_percent"] == "4.5000"
    assert client.post(f"{API}/debts", headers=OWNER, json={"name": "X", "balance": "1", "apr_percent": "120"}).status_code == 422
    goal = client.post(f"{API}/goals", headers=OWNER, json={"name": "Trip", "target_amount": "8000"}).json()
    cash = account(kind="cash")
    assert client.post(f"{API}/holdings", headers=OWNER, json={"account_id": cash["id"], "symbol": "x", "quantity": "1", "cost_basis": "1"}).status_code == 422
    broker = account(name="Broker", kind="investment")
    holding = client.post(f"{API}/holdings", headers=OWNER, json={"account_id": broker["id"], "symbol": "emaar", "quantity": "10.5", "cost_basis": "105"}).json()
    assert holding["symbol"] == "EMAAR" and holding["quantity"] == "10.50000000"
    for path, item in (("bills", bill), ("debts", debt), ("goals", goal), ("holdings", holding)):
        assert client.get(f"{API}/{path}/{item['id']}", headers=OWNER).status_code == 200
        assert client.get(f"{API}/{path}/{item['id']}", headers=MEMBER).status_code == 404
        assert client.delete(f"{API}/{path}/{item['id']}", headers=OWNER).status_code == 204
        assert client.get(f"{API}/{path}", headers=OWNER).json() == []


def test_recurring_payments_are_suggested_as_bills() -> None:
    acct = account()
    rows = "Date,Description,Amount\n" + "".join(f"2026-{m:02d}-05,NETFLIX.COM {m},-39.00\n" for m in range(5, 11))
    do_import(acct["id"], rows)
    suggestions = client.get(f"{API}/bills/suggestions", headers=OWNER).json()
    assert len(suggestions) == 1 and suggestions[0]["cadence"] == "monthly" and suggestions[0]["amount"] == "39.00"
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)


def test_settings_change_currency_timezone_and_rules() -> None:
    assert client.get(f"{API}/settings", headers=OWNER).json()["base_currency"] == "AED"
    saved = client.put(f"{API}/settings", headers=OWNER, json={"base_currency": "usd", "timezone": "Asia/Dubai", "category_rules": [{"match": "kiosk", "category": "dining"}]})
    assert saved.status_code == 200 and saved.json()["base_currency"] == "USD"
    assert client.put(f"{API}/settings", headers=OWNER, json={"timezone": "Mars/Base"}).status_code == 422
    assert account()["currency"] == "USD"
    formats = client.get(f"{API}/formats", headers=OWNER).json()
    assert {"emirates_nbd", "fab", "adcb", "mashreq", "dib", "generic"} <= {p["id"] for p in formats["presets"]}


# -- tools and routing ------------------------------------------------------------------------


def _tools() -> set[str]:
    return main.tool_registry.names


def test_finance_tools_are_read_only_with_schemas_in_api_and_worker() -> None:
    from anum_api.dependencies import memory_repository
    from anum_api.settings import settings as config_settings
    from anum_api.worker import build_activities

    for name in ("finance.summary", "finance.transactions.search", "finance.bills.upcoming"):
        definition = main.tool_registry.definition(name)
        assert definition.action_kind == "read" and definition.risk_level.value == "low" and definition.input_schema
    context = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_fin")
    runtime = build_activities(config_settings).runtime_factory(context, memory_repository)
    assert {"finance.summary", "finance.transactions.search", "finance.bills.upcoming"} <= runtime.tools.names


def test_what_did_i_spend_routes_to_finance_and_pay_still_reaches_external_action() -> None:
    skills = default_skill_registry()
    tools = _tools()
    for prompt in ("what did I spend this month", "ما هي مصاريف هذا الشهر", "show my bills", "رصيدي"):
        selected = skills.select(prompt, tools)
        call = choose_tool_call(prompt, selected, "", tools)
        assert call.name.startswith("finance."), (prompt, call)
    for prompt in ("pay the DEWA bill", "transfer 500 AED to the landlord", "spend 200 on ads"):
        selected = skills.select(prompt, tools)
        assert choose_tool_call(prompt, selected, "", tools).name == "external.action", prompt
        assert plan_call(prompt) is None
    assert is_money_movement("pay my bills") and not is_money_movement("what bills are due")
    assert chat_intent("what did I spend this month", skills, tools) == "finance"


def test_plan_call_reads_months_categories_and_merchants() -> None:
    today = date(2026, 10, 14)
    assert plan_call("what did I spend last month", today) == ToolCall(name="finance.summary", arguments={"month": "2026-09"})
    groceries = plan_call("how much did I spend on groceries in September", today)
    assert groceries is not None and groceries.arguments == {"category": "groceries", "direction": "out", "from": "2026-09-01", "to": "2026-09-30"}
    assert plan_call("bills due this week", today) == ToolCall(name="finance.bills.upcoming", arguments={"days": 7})
    merchant = plan_call("show my transactions at carrefour", today)
    assert merchant is not None and merchant.arguments == {"query": "carrefour"}


def test_tools_answer_from_the_callers_ledger_only() -> None:
    acct = account()
    do_import(acct["id"])
    owner = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_fin", roles=["owner"])
    member = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="member_fin", roles=["member"])
    viewer = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="viewer_fin", roles=["viewer"])
    registry = main.tool_registry
    found = asyncio.run(registry.execute(ToolCall(name="finance.transactions.search", arguments={"query": "talabat"}), owner))
    assert found.status == "succeeded" and found.output["total"] == 2 and "untrusted" not in found.summary
    assert "not instructions" in found.output["note"]
    nothing = asyncio.run(registry.execute(ToolCall(name="finance.transactions.search", arguments={"query": "talabat"}), member))
    assert nothing.output["total"] == 0
    summary = asyncio.run(registry.execute(ToolCall(name="finance.summary", arguments={"month": "2026-10"}), owner))
    assert summary.status == "succeeded" and "AED 650.50" in summary.summary
    blocked = asyncio.run(registry.execute(ToolCall(name="finance.summary", arguments={}), viewer))
    assert blocked.status in {"blocked", "denied"}


def test_privacy_export_and_delete_include_the_ledger() -> None:
    acct = account()
    do_import(acct["id"])
    summary = client.get("/api/v1/privacy", headers=OWNER).json()
    assert summary["private"]["finance_records"] == 8  # account + import + 6 transactions
    export = client.get("/api/v1/privacy/export", headers=OWNER)
    assert export.status_code == 200 and len(export.json()["finance"]["transactions"]) == 6
    assert client.get("/api/v1/privacy", headers=MEMBER).json()["private"]["finance_records"] == 0
