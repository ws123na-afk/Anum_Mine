"""Finance stages 3 to 5 (docs/finance.md): safe-to-spend, the Guardian kernel, read-only connectors.

In-memory stores; ``test_postgres_finance.py`` repeats isolation and the read-only scope
constraint under RLS. Fixtures are plainly fake (merchant words, round amounts); the ECB
answer below is a made-up file in the ECB's published layout.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.agent_planning import choose_tool_call
from anum_api.agent_skills import default_skill_registry
from anum_api.agent_tools import ToolCall
from anum_api.chat_tools import chat_intent
from anum_api.finance import connectors, fx, sync
from anum_api.finance.guardian import (
    MONEY_MOVING,
    SEVERITY,
    ActionKind,
    AmountLimit,
    CategoryCap,
    CoolingOff,
    GuardianAction,
    GuardianRules,
    LedgerState,
    QuietHours,
    SpendRow,
    evaluate,
    gate,
    loosening,
    payload_hash,
)
from anum_api.finance.models import Account, AccountKind, Bill, Cadence, Category, CurrencyBuffer, Debt, DebtKind, FxRate, Goal, PlanSettings
from anum_api.finance.safe_to_spend import compute, months_between
from anum_api.finance.store import finance_store
from anum_api.finance.tools import plan_call
from anum_api.schemas import TenantContext

TENANT, WORKSPACE = "tenant_fin", "workspace_fin"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_fin", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_fin", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_fin", "x-user-roles": "viewer"}
API = "/api/v1/finance"
NOW = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
client = TestClient(main.app)

ECB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <gesmes:subject>Reference rates</gesmes:subject>
  <gesmes:Sender><gesmes:name>European Central Bank</gesmes:name></gesmes:Sender>
  <Cube>
    <Cube time='{day}'>
      <Cube currency='USD' rate='1.1000'/>
      <Cube currency='JPY' rate='160.00'/>
      <Cube currency='GBP' rate='0.8500'/>
      <Cube currency='INR' rate='92.000'/>
      <Cube currency='CHF' rate='0.9500'/>
    </Cube>
  </Cube>
</gesmes:Envelope>
"""


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    yield
    finance_store.clear()
    connectors.finance_transport = None


def _owned(**values: object) -> dict[str, object]:
    return {"tenant_id": TENANT, "workspace_id": WORKSPACE, "owner_user_id": "owner_fin", "created_at": NOW, "updated_at": NOW, **values}


def _account(account_id: str, kind: AccountKind, currency: str = "AED", opening: str = "0", archived: bool = False) -> Account:
    return Account(**_owned(id=account_id, name=account_id, kind=kind, currency=currency, opening_balance=Decimal(opening), archived=archived))


def _today() -> date:
    return date(2026, 10, 14)


# -- Stage 3: safe-to-spend -----------------------------------------------------------------------


def _scenario(plan: PlanSettings | None = None, rates: list[FxRate] | None = None):  # type: ignore[no-untyped-def]
    accounts = [
        _account("current", AccountKind.BANK, opening="5000.00"), _account("wallet", AccountKind.CASH, opening="250.00"),
        _account("card", AccountKind.CARD, opening="-900.00"), _account("old", AccountKind.BANK, opening="99.00", archived=True),
        _account("usd", AccountKind.BANK, currency="USD", opening="400.00"),
    ]
    bills = [
        Bill(**_owned(id="rent", name="Rent", amount=Decimal("1500.00"), currency="AED", cadence=Cadence.MONTHLY, next_due_on=date(2026, 10, 20))),
        Bill(**_owned(id="gym", name="Gym", amount=Decimal("50.00"), currency="AED", cadence=Cadence.WEEKLY, next_due_on=date(2026, 10, 15))),
        Bill(**_owned(id="paid", name="Water", amount=Decimal("80.00"), currency="AED", cadence=Cadence.MONTHLY, next_due_on=date(2026, 10, 2))),
        Bill(**_owned(id="off", name="Paused", amount=Decimal("10.00"), currency="AED", cadence=Cadence.MONTHLY, next_due_on=date(2026, 10, 20), active=False)),
    ]
    debts = [
        Debt(**_owned(id="cc", name="Card", kind=DebtKind.CARD, balance=Decimal("900.00"), currency="AED", minimum_payment=Decimal("100.00"), due_day=25)),
        Debt(**_owned(id="loan", name="Loan", kind=DebtKind.LOAN, balance=Decimal("5000.00"), currency="AED", minimum_payment=Decimal("300.00"), due_day=5)),
        Debt(**_owned(id="friend", name="Friend", kind=DebtKind.OTHER, balance=Decimal("40.00"), currency="USD", minimum_payment=Decimal("60.00"))),
    ]
    goals = [
        Goal(**_owned(id="fund", name="Fund", target_amount=Decimal("1000.00"), saved_amount=Decimal("0"), currency="AED", target_on=date(2026, 12, 31))),
        Goal(**_owned(id="trip", name="Trip", target_amount=Decimal("500.00"), saved_amount=Decimal("100.00"), currency="AED", monthly_contribution=Decimal("200.00"))),
        Goal(**_owned(id="done", name="Done", target_amount=Decimal("10.00"), saved_amount=Decimal("10.00"), currency="AED")),
        Goal(**_owned(id="idle", name="Idle", target_amount=Decimal("10.00"), currency="AED")),
    ]
    return compute(
        today=_today(), base_currency="AED", timezone="Asia/Dubai", accounts=accounts, balances={"current": Decimal("-1000.00")},
        bills=bills, debts=debts, goals=goals, plan=plan or PlanSettings(buffers=[CurrencyBuffer(currency="AED", amount=Decimal("500.00"))]),
        rates=rates or [],
    )


def test_safe_to_spend_is_built_line_by_line_per_currency() -> None:
    result = _scenario()
    assert result.days_left == 18 and result.month_end == date(2026, 10, 31) and result.accounts_counted == 3
    aed = next(c for c in result.currencies if c.currency == "AED")
    # 4000 + 250 available; bills: rent 1500 + gym on 15, 22 and 29 (150); minimum: card 100 (loan's day 5 passed);
    # goals: fund 1000 over Oct, Nov, Dec = 333.34 (rounded up), trip 200 set; buffer 500.
    assert aed.available == Decimal("4250.00") and aed.bills == Decimal("1650.00") and aed.minimum_payments == Decimal("100.00")
    assert aed.goals == Decimal("533.34") and aed.buffer == Decimal("500.00")
    assert aed.safe_to_spend == Decimal("1466.66") and not aed.short
    assert aed.per_day == Decimal("81.48")  # 1466.66 / 18 = 81.481..., rounded down
    kinds = [line.kind for line in aed.lines]
    assert kinds == sorted(kinds, key=["balance", "bill", "minimum_payment", "goal", "buffer"].index)
    assert sum(line.amount for line in aed.lines) == aed.safe_to_spend
    derived = next(line for line in aed.lines if line.ref_id == "fund")
    assert derived.note == "goal_derived" and derived.params == {"left": "1000.00", "months": "3"}
    notes = {line.ref_id: line.note for line in result.excluded}
    assert notes == {"card": "account_card", "old": "account_archived", "loan": "minimum_passed", "done": "goal_reached", "idle": "goal_no_plan"}
    usd = next(c for c in result.currencies if c.currency == "USD")
    # A minimum payment never exceeds what is owed; no due day: counted.
    assert usd.minimum_payments == Decimal("40.00") and usd.safe_to_spend == Decimal("360.00")
    assert result.currencies[0].currency == "AED" and result.combined_status == "off" and result.combined is None


def test_short_month_has_no_per_day_and_months_between_counts_this_month() -> None:
    result = _scenario(PlanSettings(buffers=[CurrencyBuffer(currency="AED", amount=Decimal("9000.00"))]))
    aed = result.currencies[0]
    assert aed.short and aed.safe_to_spend < 0 and aed.per_day == Decimal("0.00")
    assert months_between(date(2026, 10, 14), date(2026, 10, 1)) == 1
    assert months_between(date(2026, 10, 14), date(2027, 1, 1)) == 4


def _rate(base: str, quote: str, rate: str, as_of: date, source: str = fx.ECB_SOURCE) -> FxRate:
    return FxRate(tenant_id=TENANT, workspace_id=WORKSPACE, owner_user_id="owner_fin", id=f"r_{base}{quote}{as_of}{source[:3]}",
                  base=base, quote=quote, rate=Decimal(rate), as_of=as_of, source=source, fetched_at=NOW)


def test_combined_total_uses_recorded_rates_with_date_and_source_or_says_what_is_missing() -> None:
    plan = PlanSettings(combine_currencies=True)
    missing = _scenario(plan)
    assert missing.combined_status == "missing_rates" and missing.missing_rates == ["USD/AED"] and missing.combined is None
    day = date(2026, 10, 13)
    published = fx.PublishedRates(as_of=day, base="EUR", rates={"USD": Decimal("1.1000")})
    rates = [_rate("EUR", quote, str(rate), day, source) for quote, (rate, source) in fx.with_aed_peg(published).items()]
    ready = _scenario(plan, rates)
    assert ready.combined_status == "ready" and ready.combined is not None
    usd = next(c for c in ready.combined.conversions if c.from_currency == "USD")
    # Cross through EUR: 1 USD = 1.1 x 3.6725 / 1.1 = 3.6725 AED, from the ECB and the peg.
    assert usd.rate == Decimal("3.6725000000") and usd.via == "EUR" and usd.as_of == day and "CBUAE" in usd.source
    assert usd.converted == Decimal("1322.10")  # 360.00 x 3.6725
    aed_total = next(c.safe_to_spend for c in ready.currencies if c.currency == "AED")
    assert ready.combined.safe_to_spend == aed_total + Decimal("1322.10")
    stale = _scenario(plan, [_rate("USD", "AED", "3.6725", day - timedelta(days=30), "manual")])
    assert stale.combined_status == "missing_rates"
    manual = _scenario(plan, [_rate("USD", "AED", "3.6725", day, "manual")])
    assert manual.combined_status == "ready" and manual.combined is not None and manual.combined.conversions[1].source == "manual"


def test_safe_to_spend_and_plan_api() -> None:
    assert client.get(f"{API}/safe-to-spend", headers=VIEWER).status_code == 403
    empty = client.get(f"{API}/safe-to-spend", headers=OWNER).json()
    assert empty["currencies"] == [] and empty["accounts_counted"] == 0 and "not advice" in empty["note"]
    client.post(f"{API}/accounts", headers=OWNER, json={"name": "Current", "kind": "bank", "opening_balance": "3000"})
    goal = client.post(f"{API}/goals", headers=OWNER, json={"name": "Car", "target_amount": "6000", "monthly_contribution": "250"})
    assert goal.status_code == 201 and goal.json()["monthly_contribution"] == "250.00"
    bad = client.put(f"{API}/plan", headers=OWNER, json={"buffers": [{"currency": "AED", "amount": "-5"}]})
    assert bad.status_code == 422
    twice = client.put(f"{API}/plan", headers=OWNER, json={"buffers": [{"currency": "AED", "amount": "1"}, {"currency": "aed", "amount": "2"}]})
    assert twice.status_code == 422
    saved = client.put(f"{API}/plan", headers=OWNER, json={"buffers": [{"currency": "aed", "amount": "500"}], "combine_currencies": True})
    assert saved.status_code == 200 and saved.json()["buffers"] == [{"currency": "AED", "amount": "500.00"}]
    assert client.put(f"{API}/plan", headers=VIEWER, json={}).status_code == 403
    body = client.get(f"{API}/safe-to-spend", headers=OWNER).json()
    aed = body["currencies"][0]
    assert aed["safe_to_spend"] == "2250.00" and body["combined_status"] == "single_currency"
    assert [line["kind"] for line in aed["lines"]] == ["balance", "goal", "buffer"]
    # Private: another member sees their own (empty) figure.
    assert client.get(f"{API}/safe-to-spend", headers=MEMBER).json()["currencies"] == []


# -- Stage 4: the Guardian kernel ------------------------------------------------------------------


def _action(amount: str = "100", **values: object) -> GuardianAction:
    data: dict[str, object] = {"kind": ActionKind.SPEND, "amount": Decimal(amount), "currency": "AED", "category": Category.DINING,
                               "merchant": "TALABAT ORDER 123", "at": NOW, **values}
    return GuardianAction(**data)  # type: ignore[arg-type]


STATE = LedgerState(timezone="Asia/Dubai", month_spend=[SpendRow(category=Category.DINING, currency="AED", amount=Decimal("400"))],
                    day_spend=[SpendRow(currency="AED", amount=Decimal("150"))])


def test_every_rule_kind_decides_with_a_reason() -> None:
    def codes(rules: GuardianRules, action: GuardianAction | None = None) -> tuple[str, list[str]]:
        decision = evaluate(action or _action(), rules, STATE)
        return decision.outcome, [r.code for r in decision.reasons]

    assert codes(GuardianRules()) == ("allow", [])
    assert codes(GuardianRules(allowed_currencies=["USD"])) == ("deny", ["currency_not_allowed"])
    assert codes(GuardianRules(blocked_categories=[Category.DINING])) == ("deny", ["category_blocked"])
    assert codes(GuardianRules(blocked_merchants=["Talabat"])) == ("deny", ["merchant_blocked"])
    assert codes(GuardianRules(blocked_merchants=["tala"])) == ("allow", [])  # whole words only
    assert codes(GuardianRules(max_single=[AmountLimit(currency="AED", amount="99.99")])) == ("deny", ["over_single_limit"])
    assert codes(GuardianRules(max_single=[AmountLimit(currency="AED", amount="100")])) == ("allow", [])
    assert codes(GuardianRules(max_daily_total=[AmountLimit(currency="AED", amount="249.99")])) == ("deny", ["over_daily_total"])
    assert codes(GuardianRules(max_daily_total=[AmountLimit(currency="AED", amount="250")])) == ("allow", [])
    cap = GuardianRules(category_caps=[CategoryCap(category=Category.DINING, currency="AED", amount="499")])
    assert codes(cap) == ("deny", ["over_category_cap"])
    assert codes(cap, _action(category=Category.GROCERIES)) == ("allow", [])
    assert codes(GuardianRules(approval_above=[AmountLimit(currency="AED", amount="100")])) == ("needs_approval", ["above_approval_amount"])
    # 09:00 UTC is 13:00 in Dubai.
    assert codes(GuardianRules(quiet_hours=QuietHours(start="12:00", end="14:00"))) == ("needs_approval", ["quiet_hours"])
    assert codes(GuardianRules(quiet_hours=QuietHours(start="22:00", end="13:00"))) == ("allow", [])  # wraps midnight, ends 13:00
    cool = GuardianRules(cooling_off=CoolingOff(hours=24, at_or_above=[AmountLimit(currency="AED", amount="50")]))
    assert codes(cool) == ("needs_approval", ["cooling_off"])
    assert codes(cool, _action(first_requested_at=NOW - timedelta(hours=25))) == ("allow", [])
    assert codes(GuardianRules(), _action(requested_by="agent")) == ("needs_approval", ["agent_request"])
    both = GuardianRules(blocked_merchants=["talabat"], approval_above=[AmountLimit(currency="AED", amount="1")])
    assert codes(both) == ("deny", ["merchant_blocked", "above_approval_amount"])
    assert codes(GuardianRules(), _action("0")) == ("deny", ["invalid_amount"])
    assert codes(GuardianRules(), _action(at=datetime(2026, 10, 14, 9, 0))) == ("deny", ["invalid_time"])


def test_a_record_skips_approval_and_time_rules_and_the_gate_never_moves_money() -> None:
    rules = GuardianRules(approval_above=[AmountLimit(currency="AED", amount="1")], quiet_hours=QuietHours(start="00:00", end="23:59"),
                          blocked_merchants=["talabat"])
    record = evaluate(_action(kind=ActionKind.RECORD), rules, STATE)
    assert [r.code for r in record.reasons] == ["merchant_blocked"]
    assert gate(_action(kind=ActionKind.RECORD), GuardianRules(), STATE).outcome == "allow"
    for kind in MONEY_MOVING:
        decision = gate(_action(kind=kind), GuardianRules(), STATE)
        assert decision.outcome == "deny" and decision.reasons[-1].code == "execution_not_available"


def _random_rules(rng: random.Random) -> GuardianRules:
    def limit() -> list[AmountLimit]:
        return [AmountLimit(currency=c, amount=str(rng.randint(1, 2000))) for c in rng.sample(["AED", "USD"], rng.randint(0, 2))]

    hour = rng.randint(0, 23)
    return GuardianRules(
        allowed_currencies=rng.sample(["AED", "USD", "EUR"], rng.randint(0, 2)),
        max_single=limit(), max_daily_total=limit(), approval_above=limit(),
        category_caps=[CategoryCap(category=c, currency="AED", amount=str(rng.randint(1, 3000)))
                       for c in rng.sample([Category.DINING, Category.GROCERIES, Category.FUEL], rng.randint(0, 2))],
        blocked_categories=rng.sample([Category.DINING, Category.TRAVEL], rng.randint(0, 1)),
        blocked_merchants=rng.sample(["talabat", "carrefour", "noon"], rng.randint(0, 2)),
        quiet_hours=QuietHours(start=f"{hour:02d}:00", end=f"{(hour + rng.randint(1, 8)) % 24:02d}:30") if rng.random() < 0.4 else None,
        cooling_off=CoolingOff(hours=rng.randint(1, 48), at_or_above=limit()) if rng.random() < 0.4 else None,
    )


def _tighten(rules: GuardianRules, rng: random.Random) -> GuardianRules:
    """A stricter version of ``rules``: lower limits, more blocks, wider quiet hours."""
    data = rules.model_dump(mode="json")
    for name in ("max_single", "max_daily_total", "approval_above"):
        data[name] = [{**i, "amount": str(max(Decimal("0.01"), Decimal(i["amount"]) - rng.randint(0, 500)))} for i in data[name]]
        if rng.random() < 0.3 and not any(i["currency"] == "AED" for i in data[name]):
            data[name].append({"currency": "AED", "amount": str(rng.randint(1, 500))})
    data["category_caps"] = [{**c, "amount": str(max(Decimal("0.01"), Decimal(c["amount"]) - rng.randint(0, 500)))} for c in data["category_caps"]]
    data["blocked_merchants"] = sorted(set(data["blocked_merchants"]) | set(rng.sample(["talabat", "starbucks"], rng.randint(0, 1))))
    if data["allowed_currencies"]:
        data["allowed_currencies"] = data["allowed_currencies"][:1]
    if rng.random() < 0.3:
        data["quiet_hours"] = data["quiet_hours"] or {"start": "01:00", "end": "02:00"}
    if data["cooling_off"]:
        data["cooling_off"]["hours"] = min(168, data["cooling_off"]["hours"] + rng.randint(0, 10))
    return GuardianRules.model_validate(data)


def test_property_tighter_rules_are_never_more_permissive_and_evaluation_is_deterministic() -> None:
    rng = random.Random(20261014)
    checked = 0
    for _ in range(400):
        rules = _random_rules(rng)
        stricter = _tighten(rules, rng)
        assert loosening(rules, stricter) == [], (rules, stricter)
        for _ in range(5):
            action = GuardianAction(
                kind=rng.choice(list(ActionKind)), amount=Decimal(rng.randint(1, 300000)) / 100, currency=rng.choice(["AED", "USD", "EUR"]),
                category=rng.choice([None, Category.DINING, Category.GROCERIES, Category.TRAVEL]),
                merchant=rng.choice(["TALABAT 1", "CARREFOUR", "NOON.COM", "STARBUCKS"]),
                at=NOW + timedelta(minutes=rng.randint(0, 1440)), requested_by=rng.choice(["person", "agent"]),
            )
            state = LedgerState(month_spend=[SpendRow(category=Category.DINING, currency="AED", amount=Decimal(rng.randint(0, 3000)))],
                                day_spend=[SpendRow(currency="AED", amount=Decimal(rng.randint(0, 500)))])
            loose, strict = evaluate(action, rules, state), evaluate(action, stricter, state)
            assert SEVERITY[strict.outcome] >= SEVERITY[loose.outcome], (action, rules, stricter)
            assert evaluate(action, rules, state) == loose  # same inputs, same decision
            assert SEVERITY[gate(action, rules, state).outcome] >= SEVERITY[loose.outcome]
            checked += 1
    assert checked == 2000


def test_loosening_is_detected_rule_by_rule() -> None:
    base = GuardianRules(
        allowed_currencies=["AED"], max_single=[AmountLimit(currency="AED", amount="500")],
        category_caps=[CategoryCap(category=Category.DINING, currency="AED", amount="800")], blocked_merchants=["talabat"],
        blocked_categories=[Category.TRAVEL], quiet_hours=QuietHours(start="22:00", end="06:00"),
        cooling_off=CoolingOff(hours=24, at_or_above=[AmountLimit(currency="AED", amount="1000")]),
    )
    def edit(**values: object) -> list[str]:
        return loosening(base, base.model_copy(update=values))

    assert loosening(base, base) == [] and loosening(GuardianRules(), base) == []
    assert edit(allowed_currencies=[]) == ["allowed_currencies"] and edit(allowed_currencies=["AED", "USD"]) == ["allowed_currencies"]
    assert edit(max_single=[AmountLimit(currency="AED", amount="501")]) == ["max_single"] and edit(max_single=[]) == ["max_single"]
    assert edit(category_caps=[]) == ["category_caps"]
    assert edit(blocked_merchants=[]) == ["blocked_merchants"] and edit(blocked_categories=[]) == ["blocked_categories"]
    assert edit(quiet_hours=QuietHours(start="23:00", end="06:00")) == ["quiet_hours"]
    assert edit(quiet_hours=QuietHours(start="21:00", end="07:00")) == []
    assert edit(cooling_off=CoolingOff(hours=12, at_or_above=[AmountLimit(currency="AED", amount="1000")])) == ["cooling_off"]
    assert edit(cooling_off=CoolingOff(hours=24, at_or_above=[AmountLimit(currency="AED", amount="2000")])) == ["cooling_off"]
    assert loosening(base, GuardianRules()) == [
        "allowed_currencies", "max_single", "category_caps", "blocked_categories", "blocked_merchants", "quiet_hours", "cooling_off",
    ]


def test_rules_are_validated_and_hash_binds_change_version_owner_and_rules() -> None:
    with pytest.raises(ValueError):
        GuardianRules(max_single=[AmountLimit(currency="AED", amount="1"), AmountLimit(currency="AED", amount="2")])
    with pytest.raises(ValueError):
        AmountLimit(currency="AED", amount="0")
    with pytest.raises(ValueError):
        QuietHours(start="10:00", end="10:00")
    with pytest.raises(ValueError):
        GuardianRules.model_validate({"unknown": 1})
    rules = GuardianRules(blocked_merchants=["  Talabat  ", "talabat"], allowed_currencies=["usd", "aed"])
    assert rules.blocked_merchants == ["talabat"] and rules.allowed_currencies == ["AED", "USD"]
    first = payload_hash(change_id="c1", owner_user_id="u", base_version=0, rules=rules)
    assert len(first) == 64 and first == payload_hash(change_id="c1", owner_user_id="u", base_version=0, rules=rules)
    others = {
        payload_hash(change_id="c2", owner_user_id="u", base_version=0, rules=rules),
        payload_hash(change_id="c1", owner_user_id="v", base_version=0, rules=rules),
        payload_hash(change_id="c1", owner_user_id="u", base_version=1, rules=rules),
        payload_hash(change_id="c1", owner_user_id="u", base_version=0, rules=GuardianRules()),
    }
    assert first not in others and len(others) == 4


def _propose(rules: dict, headers: dict = OWNER) -> dict:
    response = client.post(f"{API}/guardian/changes", headers=headers, json={"rules": rules, "reason": "Keep dining in check"})
    assert response.status_code == 201, response.text
    return response.json()


def test_rule_changes_need_the_persons_confirmation_with_the_payload_hash() -> None:
    from anum_api.dependencies import memory_repository

    assert client.get(f"{API}/guardian", headers=OWNER).json()["version"] == 0
    assert client.post(f"{API}/guardian/changes", headers=VIEWER, json={"rules": {}}).status_code == 403
    assert client.post(f"{API}/guardian/changes", headers=OWNER, json={"rules": {"max_single": [{"currency": "AED", "amount": "-1"}]}}).status_code == 422
    change = _propose({"category_caps": [{"category": "dining", "currency": "AED", "amount": "800"}]})
    assert change["status"] == "pending" and change["loosens"] == [] and change["base_version"] == 0
    # Nothing changed yet.
    view = client.get(f"{API}/guardian", headers=OWNER).json()
    assert view["version"] == 0 and [c["id"] for c in view["pending"]] == [change["id"]]
    wrong = client.post(f"{API}/guardian/changes/{change['id']}/confirm", headers=OWNER, json={"payload_hash": "0" * 64})
    assert wrong.status_code == 409
    # Another member cannot confirm (or even see) the owner's change.
    assert client.post(f"{API}/guardian/changes/{change['id']}/confirm", headers=MEMBER, json={"payload_hash": change["payload_hash"]}).status_code == 404
    assert client.post(f"{API}/guardian/changes/{change['id']}/confirm", headers=VIEWER, json={"payload_hash": change["payload_hash"]}).status_code == 403
    stale = _propose({"blocked_merchants": ["talabat"]})
    done = client.post(f"{API}/guardian/changes/{change['id']}/confirm", headers=OWNER, json={"payload_hash": change["payload_hash"]})
    assert done.status_code == 200 and done.json()["version"] == 1 and done.json()["pending"] == []
    assert done.json()["rules"]["category_caps"] == [{"currency": "AED", "amount": "800.00", "category": "dining"}]
    # The other change was made on version 0: superseded, never applied.
    again = client.post(f"{API}/guardian/changes/{stale['id']}/confirm", headers=OWNER, json={"payload_hash": stale["payload_hash"]})
    assert again.status_code == 409 and "superseded" in again.json()["error"]["message"]
    loosen = _propose({})
    assert loosen["loosens"] == ["category_caps"]
    rejected = client.post(f"{API}/guardian/changes/{loosen['id']}/reject", headers=OWNER)
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    versions = client.get(f"{API}/guardian/versions", headers=OWNER).json()
    assert versions["total"] == 1 and versions["items"][0]["confirmed_by"] == "owner_fin"
    audits = [r for r in memory_repository.store.audit_records if r.action.startswith("finance.guardian")]
    assert {r.action for r in audits} >= {"finance.guardian.propose", "finance.guardian.confirm", "finance.guardian.reject"}
    for record in audits:
        assert "800" not in str(record.metadata) and "talabat" not in str(record.metadata)


def test_dry_run_evaluation_and_transaction_warnings_and_alerts() -> None:
    change = _propose({
        "category_caps": [{"category": "dining", "currency": "AED", "amount": "100"}], "blocked_merchants": ["talabat"],
        "max_single": [{"currency": "AED", "amount": "1000"}],
    })
    client.post(f"{API}/guardian/changes/{change['id']}/confirm", headers=OWNER, json={"payload_hash": change["payload_hash"]})
    acct = client.post(f"{API}/accounts", headers=OWNER, json={"name": "Current", "kind": "bank"}).json()
    today = client.get(f"{API}/summary", headers=OWNER).json()["today"]
    created = client.post(f"{API}/transactions", headers=OWNER, json={"account_id": acct["id"], "posted_on": today, "amount": "-60", "description": "TALABAT ORDER"})
    assert created.status_code == 201
    warning = created.json()["guardian"]
    assert warning["outcome"] == "deny" and [r["code"] for r in warning["reasons"]] == ["merchant_blocked"]
    ok = client.post(f"{API}/transactions", headers=OWNER, json={"account_id": acct["id"], "posted_on": today, "amount": "-30", "description": "CAFE", "category": "dining"})
    assert ok.json()["guardian"]["outcome"] == "allow"
    income = client.post(f"{API}/transactions", headers=OWNER, json={"account_id": acct["id"], "posted_on": today, "amount": "500", "description": "SALARY"})
    assert income.json()["guardian"] is None
    check = client.post(f"{API}/guardian/evaluate", headers=OWNER, json={"amount": "20", "category": "dining", "merchant": "Cafe"})
    body = check.json()
    # 60 + 30 already on dining; 20 more passes the cap of 100.
    assert check.status_code == 200 and body["rules_decision"]["outcome"] == "deny" and body["version"] == 1
    assert body["decision"]["reasons"][-1]["code"] == "execution_not_available" and "nothing was paid" in body["note"]
    record = client.post(f"{API}/guardian/evaluate", headers=OWNER, json={"kind": "record", "amount": "5", "category": "groceries"})
    assert record.json()["decision"]["outcome"] == "allow"
    assert client.post(f"{API}/guardian/evaluate", headers=VIEWER, json={"amount": "5"}).status_code == 403
    assert client.post(f"{API}/guardian/evaluate", headers=OWNER, json={"amount": "-5"}).status_code == 422
    alerts = client.get(f"{API}/guardian/alerts", headers=OWNER).json()["alerts"]
    assert {a["code"] for a in alerts} == {"merchant_blocked_seen", "cap_near"}
    imported = client.post(f"{API}/imports", headers=OWNER, json={
        "account_id": acct["id"], "filename": "x.csv", "content": f"Date,Description,Amount\n{today},TALABAT LATE,-10\n{today},BIG TV,-2000\n",
    })
    assert imported.status_code == 201 and imported.json()["rule_breaks"] == 2


def test_agents_read_and_propose_stricter_rules_only_and_never_confirm() -> None:
    owner = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_fin", roles=["owner"])
    viewer = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="viewer_fin", roles=["viewer"])
    registry = main.tool_registry
    names = registry.names
    assert {"finance.safe_to_spend", "finance.guardian.rules", "finance.guardian.check", "finance.guardian.propose"} <= names
    assert not [n for n in names if n.startswith("finance.") and any(w in n for w in ("confirm", "pay", "transfer", "trade", "connect", "credential"))]
    for name in ("finance.safe_to_spend", "finance.guardian.rules", "finance.guardian.check"):
        assert registry.definition(name).action_kind == "read"
    proposed = asyncio.run(registry.execute(ToolCall(name="finance.guardian.propose", arguments={"edit": {"type": "category_cap", "category": "dining", "amount": "700"}}), owner))
    assert proposed.status == "succeeded" and "confirm it yourself" in proposed.summary
    pending = client.get(f"{API}/guardian", headers=OWNER).json()
    assert pending["version"] == 0 and pending["pending"][0]["proposed_by"] == "agent"
    change = pending["pending"][0]
    client.post(f"{API}/guardian/changes/{change['id']}/confirm", headers=OWNER, json={"payload_hash": change["payload_hash"]})
    # Raising the cap would loosen it: refused for an assistant, whatever it asks.
    looser = asyncio.run(registry.execute(ToolCall(name="finance.guardian.propose", arguments={"edit": {"type": "category_cap", "category": "dining", "amount": "900"}}), owner))
    assert looser.status == "blocked" and "Only you can loosen" in looser.summary
    tighter = asyncio.run(registry.execute(ToolCall(name="finance.guardian.propose", arguments={"edit": {"type": "category_cap", "category": "dining", "amount": "500"}}), owner))
    assert tighter.status == "succeeded"
    rules = asyncio.run(registry.execute(ToolCall(name="finance.guardian.rules", arguments={}), owner))
    assert "version 1" in rules.summary and "1 change(s) wait" in rules.summary
    check = asyncio.run(registry.execute(ToolCall(name="finance.guardian.check", arguments={"amount": "800", "category": "dining"}), owner))
    assert check.status == "succeeded" and "nothing was done" in check.summary and "do not allow" in check.summary
    assert asyncio.run(registry.execute(ToolCall(name="finance.guardian.propose", arguments={"edit": {"type": "max_single", "amount": "5"}}), viewer)).status == "blocked"
    safe = asyncio.run(registry.execute(ToolCall(name="finance.safe_to_spend", arguments={}), owner))
    assert safe.status == "succeeded" and "add a bank or cash account" in safe.summary


def test_requests_route_to_safe_to_spend_rules_and_proposals_in_english_and_arabic() -> None:
    skills = default_skill_registry()
    tools = main.tool_registry.names
    expected = {
        "how much can I spend for the rest of the month": "finance.safe_to_spend",
        "what's safe to spend": "finance.safe_to_spend",
        "كم أقدر أصرف هذا الشهر": "finance.safe_to_spend",
        "ما المتاح للصرف": "finance.safe_to_spend",
        "show my spending rules": "finance.guardian.rules",
        "ما هي قواعد الصرف": "finance.guardian.rules",
        "cap dining at 600": "finance.guardian.propose",
        "block payments to talabat": "finance.guardian.propose",
    }
    for prompt, tool in expected.items():
        call = choose_tool_call(prompt, skills.select(prompt, tools), "", tools)
        assert call.name == tool, (prompt, call)
    assert plan_call("never spend more than 2000 at once") == ToolCall(name="finance.guardian.propose", arguments={"edit": {"type": "max_single", "amount": "2000"}})
    assert plan_call("block my calendar tomorrow") is None
    assert chat_intent("how much can I spend this month", skills, tools) == "finance"
    for prompt in ("pay the DEWA bill", "transfer 500 AED to the landlord"):
        assert choose_tool_call(prompt, skills.select(prompt, tools), "", tools).name == "external.action"


# -- Stage 5: connectors --------------------------------------------------------------------------


def _ecb(day: str, status: int = 200, body: str | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL(fx.ECB_URL) and request.method == "GET"
        return httpx.Response(status, text=body if body is not None else ECB_XML.format(day=day))

    return httpx.MockTransport(handler)


def test_ecb_parser_is_strict_and_adds_the_dirham_through_the_peg() -> None:
    today = date(2026, 10, 14)
    published = fx.parse_ecb(ECB_XML.format(day="2026-10-13"), today)
    assert published.as_of == date(2026, 10, 13) and published.rates["USD"] == Decimal("1.1000000000")
    rates = fx.with_aed_peg(published)
    assert rates["AED"] == (Decimal("4.0397500000"), fx.PEG_SOURCE) and rates["USD"][1] == fx.ECB_SOURCE
    for bad in (
        ECB_XML.format(day="2026-09-01"),  # too old
        ECB_XML.format(day="2026-10-20"),  # in the future
        ECB_XML.format(day="2026-10-13").replace("rate='1.1000'", "rate='-1'"),  # not a rate: USD missing
        ECB_XML.format(day="2026-10-13").replace("JPY", "USD"),  # listed twice
        "<html>maintenance</html>",
        ECB_XML.format(day="2026-10-13") + " " * 70_000,
    ):
        with pytest.raises(fx.FxError):
            fx.parse_ecb(bad, today)
    with pytest.raises(fx.FxError):
        fx.check_rate("0")


def test_connector_registry_is_read_only_and_unbuilt_sources_cannot_be_connected() -> None:
    providers = client.get(f"{API}/connectors", headers=MEMBER).json()
    assert {p["id"] for p in providers} == set(connectors.PROVIDERS)
    for provider in providers:
        assert provider["scopes"] and all(scope.endswith(":read") for scope in provider["scopes"])
        assert provider["available"] == (provider["id"] == "ecb_fx")
        if not provider["available"]:
            assert provider["owner_action"]
    refused = client.post(f"{API}/connections", headers=OWNER, json={"provider": "uae_open_finance_bank"})
    assert refused.status_code == 409 and "not available yet" in refused.json()["error"]["message"]
    assert client.post(f"{API}/connections", headers=OWNER, json={"provider": "nope"}).status_code == 422
    assert client.post(f"{API}/connections", headers=VIEWER, json={"provider": "ecb_fx"}).status_code == 403
    assert connectors.is_read_only(["fx_rates:read"]) and not connectors.is_read_only(["orders:write"]) and not connectors.is_read_only([])


def test_ecb_connection_syncs_rates_records_health_and_deletes_cleanly() -> None:
    from anum_api.dependencies import memory_repository

    created = client.post(f"{API}/connections", headers=OWNER, json={"provider": "ecb_fx"})
    assert created.status_code == 201
    connection = created.json()
    assert connection["status"] == "active" and connection["has_credentials"] is False and "credentials_encrypted" not in connection
    assert client.post(f"{API}/connections", headers=OWNER, json={"provider": "ecb_fx"}).status_code == 409
    today = client.get(f"{API}/summary", headers=OWNER).json()["today"]
    connectors.finance_transport = _ecb(today)
    report = client.post(f"{API}/connections/{connection['id']}/sync", headers=OWNER).json()
    assert report["status"] == "ok" and report["counts"]["rates_new"] == 6 and report["counts"]["rates_seen"] == 6
    again = client.post(f"{API}/connections/{connection['id']}/sync", headers=OWNER).json()
    assert again["counts"]["rates_new"] == 0  # the same day's rates are not stored twice
    rates = client.get(f"{API}/fx-rates", headers=OWNER).json()
    aed = next(r for r in rates if r["quote"] == "AED")
    assert aed["base"] == "EUR" and aed["source"] == fx.PEG_SOURCE and aed["as_of"] == today and aed["source_url"] == fx.ECB_URL
    assert client.get(f"{API}/fx-rates", headers=MEMBER).json() == []
    healthy = client.get(f"{API}/connections/{connection['id']}", headers=OWNER).json()
    assert healthy["last_success_at"] and healthy["last_error"] is None
    # A broken answer is recorded as an error with a safe message; rates stay.
    connectors.finance_transport = _ecb(today, status=503)
    failed = client.post(f"{API}/connections/{connection['id']}/sync", headers=OWNER).json()
    assert failed["status"] == "error" and "503" in failed["message"]
    broken = client.get(f"{API}/connections/{connection['id']}", headers=OWNER).json()
    assert broken["status"] == "error" and "503" in broken["last_error"]
    connectors.finance_transport = _ecb(today, body="<Cube time='2026-10-13'><Cube currency='USD' rate='abc'/></Cube>")
    assert client.post(f"{API}/connections/{connection['id']}/sync", headers=OWNER).json()["status"] == "error"
    # Safe-to-spend combines currencies with the fetched rates.
    client.post(f"{API}/accounts", headers=OWNER, json={"name": "AED", "kind": "bank", "opening_balance": "100"})
    client.post(f"{API}/accounts", headers=OWNER, json={"name": "USD", "kind": "bank", "currency": "USD", "opening_balance": "10"})
    client.put(f"{API}/plan", headers=OWNER, json={"combine_currencies": True})
    combined = client.get(f"{API}/safe-to-spend", headers=OWNER).json()
    assert combined["combined_status"] == "ready" and combined["combined"]["safe_to_spend"] == "136.72"  # 36.725 rounds half to even
    disconnected = client.post(f"{API}/connections/{connection['id']}/disconnect", headers=OWNER).json()
    assert disconnected["status"] == "disconnected" and disconnected["enabled"] is False
    assert client.post(f"{API}/connections/{connection['id']}/sync", headers=OWNER).status_code == 409
    assert client.delete(f"{API}/connections/{connection['id']}", headers=MEMBER).status_code == 404
    assert client.delete(f"{API}/connections/{connection['id']}", headers=OWNER).status_code == 204
    assert client.get(f"{API}/fx-rates", headers=OWNER).json() == []
    actions = {r.action for r in memory_repository.store.audit_records if r.action.startswith("finance.connection")}
    assert actions >= {"finance.connection.create", "finance.connection.sync", "finance.connection.disconnect", "finance.connection.delete"}


def test_manual_rates_are_checked() -> None:
    today = client.get(f"{API}/summary", headers=OWNER).json()["today"]
    added = client.post(f"{API}/fx-rates", headers=OWNER, json={"base": "usd", "quote": "AED", "rate": "3.6725", "as_of": today})
    assert added.status_code == 201 and added.json()["source"] == "manual" and added.json()["rate"] == "3.6725000000"
    assert client.post(f"{API}/fx-rates", headers=OWNER, json={"base": "USD", "quote": "AED", "rate": "3.6725", "as_of": today}).status_code == 409
    for bad in ({"rate": "0"}, {"rate": "abc"}, {"quote": "USD"}, {"as_of": "2030-01-01"}):
        assert client.post(f"{API}/fx-rates", headers=OWNER, json={"base": "USD", "quote": "AED", "rate": "3.6725", "as_of": today, **bad}).status_code == 422
    assert client.delete(f"{API}/fx-rates/{added.json()['id']}", headers=OWNER).status_code == 204


def test_credentials_are_encrypted_never_returned_and_imports_dedupe(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider that needs credentials, registered for this test only (no such adapter ships)."""
    spec = connectors.ProviderSpec(
        id="test_bank", kind=connectors.ConnectorKind.BANK, name="Test bank", summary="test", scopes=("transactions:read",),
        available=True, needs_credentials=True, credential_fields=("api_key",),
    )
    seen: list[dict[str, str]] = []

    async def adapter(connection, credentials, today):  # type: ignore[no-untyped-def]
        seen.append(credentials)
        return connectors.FetchResult(transactions=[
            connectors.RemoteTransaction(external_id="t1", account_ref="main", posted_on=today, amount=Decimal("-12.50"), currency="AED", description="CARREFOUR‮"),
            connectors.RemoteTransaction(external_id="t2", account_ref="main", posted_on=today, amount=Decimal("-12.50"), currency="AED", description="CARREFOUR"),
            connectors.RemoteTransaction(external_id="t3", account_ref="unknown", posted_on=today, amount=Decimal("-1"), currency="AED", description="X"),
        ])

    monkeypatch.setitem(connectors.PROVIDERS, "test_bank", spec)
    monkeypatch.setitem(connectors.ADAPTERS, "test_bank", adapter)
    secret = "example-key-for-tests-only"
    assert client.post(f"{API}/connections", headers=OWNER, json={"provider": "test_bank"}).status_code == 422
    created = client.post(f"{API}/connections", headers=OWNER, json={"provider": "test_bank", "credentials": {"api_key": secret}})
    assert created.status_code == 201 and secret not in created.text and created.json()["has_credentials"] is True
    stored = finance_store.connections[created.json()["id"]]
    assert stored.credentials_encrypted and secret not in stored.credentials_encrypted
    acct = client.post(f"{API}/accounts", headers=OWNER, json={"name": "Main", "kind": "bank"}).json()
    stored.settings = {"accounts": {"main": acct["id"]}}
    first = client.post(f"{API}/connections/{created.json()['id']}/sync", headers=OWNER).json()
    assert seen == [{"api_key": secret}]
    assert first["counts"] == {"rates_new": 0, "rates_seen": 0, "transactions_new": 2, "transactions_duplicate": 0, "transactions_unmapped": 1}
    second = client.post(f"{API}/connections/{created.json()['id']}/sync", headers=OWNER).json()
    assert second["counts"]["transactions_new"] == 0 and second["counts"]["transactions_duplicate"] == 2
    rows = client.get(f"{API}/transactions", headers=OWNER).json()["items"]
    assert {r["description"] for r in rows} == {"CARREFOUR"} and {r["category"] for r in rows} == {"groceries"}
    for path in ("/connections", f"/connections/{created.json()['id']}"):
        assert secret not in client.get(f"{API}{path}", headers=OWNER).text
    export = client.get("/api/v1/privacy/export", headers=OWNER)
    assert export.status_code == 200 and secret not in export.text and stored.credentials_encrypted not in export.text
    assert export.json()["finance"]["connections"][0]["has_credentials"] is True
    client.post(f"{API}/connections/{created.json()['id']}/disconnect", headers=OWNER)
    assert finance_store.connections[created.json()["id"]].credentials_encrypted is None


def test_scheduler_syncs_due_connections_in_their_owners_scope() -> None:
    created = client.post(f"{API}/connections", headers=OWNER, json={"provider": "ecb_fx"}).json()
    today = client.get(f"{API}/summary", headers=OWNER).json()["today"]
    connectors.finance_transport = _ecb(today)
    assert asyncio.run(sync.run_once()) == 1
    assert asyncio.run(sync.run_once()) == 0  # next sync in 12 hours
    assert finance_store.connections[created["id"]].last_success_at is not None
    assert len(client.get(f"{API}/fx-rates", headers=OWNER).json()) == 6
    assert sync.build_finance_scheduler() is None  # off unless ANUM_FINANCE_SYNC_ENABLED=true


def test_privacy_counts_exports_and_deletes_every_new_table() -> None:
    _propose({"blocked_merchants": ["talabat"]})
    client.put(f"{API}/plan", headers=OWNER, json={"buffers": [{"currency": "AED", "amount": "100"}]})
    client.post(f"{API}/connections", headers=OWNER, json={"provider": "ecb_fx"})
    today = client.get(f"{API}/summary", headers=OWNER).json()["today"]
    client.post(f"{API}/fx-rates", headers=OWNER, json={"base": "USD", "quote": "AED", "rate": "3.6725", "as_of": today})
    assert client.get("/api/v1/privacy", headers=OWNER).json()["private"]["finance_records"] == 4
    export = client.get("/api/v1/privacy/export", headers=OWNER).json()["finance"]
    assert export["plan"]["buffers"] and len(export["guardian_changes"]) == 1 and len(export["connections"]) == 1 and len(export["fx_rates"]) == 1
    deleted = client.post("/api/v1/privacy/delete", headers=OWNER, json={"confirm": "DELETE MY DATA"})
    assert deleted.status_code == 200
    assert client.get("/api/v1/privacy", headers=OWNER).json()["private"]["finance_records"] == 0


def test_the_worker_registers_the_same_finance_tools_as_the_api() -> None:
    from anum_api.dependencies import memory_repository
    from anum_api.settings import settings as config_settings
    from anum_api.worker import build_activities

    context = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_fin")
    runtime = build_activities(config_settings).runtime_factory(context, memory_repository)
    finance = {name for name in main.tool_registry.names if name.startswith("finance.")}
    assert len(finance) == 7 and finance <= runtime.tools.names
