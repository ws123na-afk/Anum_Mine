"""Loan schedules with interest, overdue and repayment suggestions, card interest and expected income in the forecast
(docs/finance.md#lent-and-borrowed, #suggestions, #forecast). In-memory stores; every record is SYNTHETIC.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

from test_finance_personal_money import ME, OWNER, VIEWER, account, clean, client, record  # noqa: F401  (``clean`` resets the stores)

from anum_api.finance import forecast as F
from anum_api.finance import personal as P
from anum_api.finance.models import Debt, DebtKind
from anum_api.schemas import utc_now

API = "/api/v1/finance"
STAMP = datetime(2026, 11, 1, tzinfo=UTC)


def today() -> date:
    return utc_now().date()


def loan(**body: object) -> P.PersonLoan:
    base = {"tenant_id": "t", "workspace_id": "w", "owner_user_id": "u", "id": "loan_1", "direction": P.LoanDirection.LENT, "person": "Synthetic Friend",
            "amount": D("1200"), "currency": "AED", "lent_on": date(2026, 1, 1), "created_at": STAMP, "updated_at": STAMP}
    return P.PersonLoan(**{**base, **body})


class Link:
    def __init__(self, amount: str, currency: str = "AED", role: str = "deposit") -> None:
        self.target_id, self.role, self.amount, self.currency, self.status = "loan_1", role, D(amount), currency, "active"


# -- the schedule, worked out by hand -------------------------------------------------------------------------------------


def test_an_interest_free_schedule_splits_the_amount_and_the_last_repayment_takes_the_rounding() -> None:
    rows = P.loan_schedule(loan(amount=D("1000"), installments=3, first_due_on=date(2026, 1, 31)))
    assert [(r.due_on, r.amount) for r in rows] == [(date(2026, 1, 31), D("333.33")), (date(2026, 2, 28), D("333.33")), (date(2026, 3, 31), D("333.34"))]
    assert sum(r.interest for r in rows) == 0


def test_an_annuity_at_12_percent_matches_the_standard_formula() -> None:
    # 1,200 at 12% a year over 12 months: 1200 x 0.01 / (1 - 1.01^-12) = 106.62 a month; interest 12.00 in the first month.
    rows = P.loan_schedule(loan(interest_rate=D("12"), installments=12, first_due_on=date(2026, 2, 1)))
    assert rows[0].amount == D("106.62") and rows[0].interest == D("12.00") and rows[0].principal == D("94.62")
    assert sum(r.principal for r in rows) == D("1200.00")
    assert sum(r.interest for r in rows) == D("79.42")


def test_a_single_due_date_with_interest_is_simple_interest_for_the_days() -> None:
    # 1,200 at 10% for 73 days (1 Jan to 15 Mar): 1200 x 0.10 x 73 / 365 = 24.00.
    rows = P.loan_schedule(loan(interest_rate=D("10"), due_on=date(2026, 3, 15)))
    assert [(r.due_on, r.interest, r.amount) for r in rows] == [(date(2026, 3, 15), D("24.00"), D("1224.00"))]


def test_repayments_cover_the_schedule_in_date_order_and_the_rest_is_overdue() -> None:
    item = loan(amount=D("900"), installments=3, first_due_on=date(2026, 1, 31))
    f = P.loan_figures(item, [Link("400")], date(2026, 3, 5))
    # 300 + 300 were due by 5 March; 400 repaid covers the first and 100 of the second.
    assert (f.status, f.overdue_amount, f.next_due_on, f.next_due_amount, f.outstanding, f.paid_through) == (
        "overdue", D("200.00"), date(2026, 2, 28), D("200.00"), D("500.00"), 1)
    # Money in another currency never reduces what is owed.
    assert P.loan_figures(item, [Link("900", currency="USD")], date(2026, 3, 5)).outstanding == D("900.00")
    assert P.loan_figures(item, [Link("900")], date(2026, 3, 5)).status == "repaid"


def test_the_api_saves_a_schedule_refuses_half_of_one_and_lists_overdue_per_currency() -> None:
    lent_on = today() - timedelta(days=90)
    made = client.post(f"{API}/people-loans", headers=OWNER, json={
        "direction": "lent", "person": "Synthetic Cousin", "amount": "600", "currency": "AED", "lent_on": lent_on.isoformat(),
        "installments": 3, "first_due_on": (lent_on + timedelta(days=30)).isoformat(), "interest_rate": "0"})
    assert made.status_code == 201, made.text
    view = made.json()
    assert (view["installments"], view["interest_rate"], view["status"]) == (3, None, "overdue")
    assert view["due_on"] == P.schedule_dates(lent_on + timedelta(days=30), 3)[-1].isoformat()
    detail = client.get(f"{API}/people-loans/{view['id']}", headers=OWNER).json()
    assert [row["state"] for row in detail["schedule"]].count("overdue") >= 2 and len(detail["schedule"]) == 3
    page = client.get(f"{API}/people-loans", headers=OWNER).json()
    owed = next(o for o in page["owed"] if o["currency"] == "AED")
    assert owed["they_owe_me"] == "600.00" and D(owed["they_owe_me_overdue"]) >= D("400")
    half = client.post(f"{API}/people-loans", headers=OWNER, json={"direction": "lent", "person": "X", "amount": "10", "lent_on": lent_on.isoformat(),
                                                                   "installments": 2})
    assert half.status_code == 422
    rate_only = client.post(f"{API}/people-loans", headers=OWNER, json={"direction": "lent", "person": "X", "amount": "10",
                                                                        "lent_on": lent_on.isoformat(), "interest_rate": "5"})
    assert rate_only.status_code == 422 and "due date" in rate_only.json()["error"]["message"]
    with_interest = client.post(f"{API}/people-loans", headers=OWNER, json={
        "direction": "borrowed", "person": "Synthetic Uncle", "amount": "1200", "currency": "AED", "lent_on": lent_on.isoformat(),
        "installments": 12, "first_due_on": (lent_on + timedelta(days=31)).isoformat(), "interest_rate": "12"}).json()
    assert with_interest["interest"] == "79.42" and with_interest["total_due"] == "1279.42"


# -- the Suggestions queue ------------------------------------------------------------------------------------------------


def queue(kind: str) -> list[dict]:
    response = client.get(f"{API}/suggestions", headers=OWNER, params={"kind": kind, "limit": 100})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_an_overdue_repayment_and_a_matching_transaction_are_suggested_and_linked_only_on_accept() -> None:
    bank = account(balance="5000")
    lent_on = today() - timedelta(days=60)
    made = client.post(f"{API}/people-loans", headers=OWNER, json={
        "direction": "lent", "person": "Synthetic Neighbour", "amount": "500", "currency": "AED", "lent_on": lent_on.isoformat(),
        "due_on": (today() - timedelta(days=5)).isoformat()}).json()
    overdue = queue("loan_overdue")
    assert [(i["params"]["person"], i["params"]["overdue"], i["action"]["kind"]) for i in overdue] == [("Synthetic Neighbour", "500.00", "acknowledge")]
    # Money in naming the person: proposed as a repayment, nothing linked yet.
    record(bank["id"], "200", (today() - timedelta(days=2)).isoformat(), "Transfer from SYNTHETIC NEIGHBOUR")
    record(bank["id"], "200", (today() - timedelta(days=2)).isoformat(), "Salary")  # unrelated money in
    record(bank["id"], "300", (today() - timedelta(days=1)).isoformat(), "Transfer from Synthetic Neighbour", category="other")
    # In another currency: never proposed.
    usd = account(currency="USD", name="USD", balance="0")
    record(usd["id"], "100", today().isoformat(), "Transfer from Synthetic Neighbour")
    found = queue("loan_repayment")
    assert sorted(i["params"]["amount"] for i in found) == ["200.00", "300.00"]
    assert all(i["action"]["link"]["dimension"] == "person_loan" and i["action"]["link"]["role"] == "deposit" for i in found)
    assert client.get(f"{API}/people-loans/{made['id']}", headers=OWNER).json()["repaid"] == "0.00"
    first = found[0]
    assert client.post(f"{API}/suggestions/accept", headers=VIEWER, json={"key": first["key"], "payload_hash": first["payload_hash"]}).status_code == 403
    accepted = client.post(f"{API}/suggestions/accept", headers=OWNER, json={"key": first["key"], "payload_hash": first["payload_hash"]})
    assert accepted.status_code == 200, accepted.text and accepted.json()["link_id"]
    detail = client.get(f"{API}/people-loans/{made['id']}", headers=OWNER).json()
    assert detail["repaid"] == first["params"]["amount"] and len(detail["movements"]) == 1
    assert len(queue("loan_repayment")) == 1


def test_a_borrowed_loan_matches_money_out_by_the_exact_next_repayment() -> None:
    bank = account(balance="5000")
    lent_on = today() - timedelta(days=40)
    client.post(f"{API}/people-loans", headers=OWNER, json={
        "direction": "borrowed", "person": "Synthetic Brother", "amount": "900", "currency": "AED", "lent_on": lent_on.isoformat(),
        "installments": 3, "first_due_on": (today() + timedelta(days=3)).isoformat()})
    record(bank["id"], "-300", today().isoformat(), "Payment")
    record(bank["id"], "-299", today().isoformat(), "Payment")
    found = queue("loan_repayment")
    assert [(i["params"]["amount"], i["confidence"], i["action"]["link"]["role"]) for i in found] == [("300.00", "0.5", "payment")]


# -- the forecast ----------------------------------------------------------------------------------------------------------


def inputs(**extra: object) -> F.ForecastInputs:
    base: dict = {"today": date(2026, 11, 9), "timezone": "Asia/Dubai", "base_currency": "AED", "accounts": [], "balances": {}, "transactions": [],
                  "bills": [], "debts": [], "goals": []}
    return F.ForecastInputs(**{**base, **extra})


def card(apr: str, minimum: str = "100", balance: str = "1000", due_day: int | None = 20) -> Debt:
    return Debt(tenant_id="t", workspace_id="w", owner_user_id="u", id="card_1", name="Synthetic Card", kind=DebtKind.CARD, balance=D(balance),
                currency="AED", apr_percent=D(apr), minimum_payment=D(minimum), due_day=due_day, created_at=STAMP, updated_at=STAMP)


def test_card_interest_is_estimated_only_with_a_known_rate() -> None:
    charges, left = F.card_interest(inputs(debts=[card("24")]), "AED", F.horizon_end(date(2026, 11, 9)))
    # (1000 - 100) x 2% = 18.00 on 20 Nov; (918 - 100) x 2% = 16.36 on 20 Dec; (834.36 - 100) x 2% = 14.69 on 20 Jan.
    assert [(c.on, c.amount) for c in charges] == [(date(2026, 11, 20), D("18.00")), (date(2026, 12, 20), D("16.36")), (date(2027, 1, 20), D("14.69"))]
    assert left == []
    charges, left = F.card_interest(inputs(debts=[card("0")]), "AED", F.horizon_end(date(2026, 11, 9)))
    assert charges == [] and [a.code for a in left] == ["card_apr_unknown"]


def test_expected_income_counts_on_its_date_never_on_the_low_line_and_a_late_one_is_named() -> None:
    made = [P.ExpectedIncome(tenant_id="t", workspace_id="w", owner_user_id="u", id=f"x{n}", label=label, amount=D("2000"), currency="AED",
                             expected_on=on, received_on=received, created_at=STAMP, updated_at=STAMP)
            for n, (label, on, received) in enumerate((("Synthetic bonus", date(2026, 11, 25), None), ("Old refund", date(2026, 11, 1), None),
                                                       ("Received sale", date(2026, 11, 20), date(2026, 11, 8))))]
    result = F.build(inputs(expected_income=made))
    row = result.currencies[0]
    assert [(e.kind, e.label, e.on) for e in row.events] == [("expected_income", "Synthetic bonus", date(2026, 11, 25))]
    end = next(p for p in row.points if p.on == date(2026, 11, 30))
    assert (end.expected, end.low) == (D("2000.00"), D("0.00"))
    assert [a.code for a in row.left_out] == ["expected_income_late"]


def test_expected_income_api_is_owner_only_and_reaches_the_forecast() -> None:
    account(balance="100")
    on = today() + timedelta(days=3)
    made = client.post(f"{API}/expected-income", headers=OWNER, json={"label": "Synthetic bonus", "amount": "750", "currency": "AED",
                                                                      "expected_on": on.isoformat()})
    assert made.status_code == 201, made.text and made.json()["state"] == "expected"
    assert client.post(f"{API}/expected-income", headers=VIEWER, json={"label": "X", "amount": "1", "expected_on": on.isoformat()}).status_code == 403
    forecast = client.get(f"{API}/forecast", headers=OWNER).json()
    events = [e for c in forecast["currencies"] for e in c["events"] if e["kind"] == "expected_income"]
    assert [(e["label"], e["amount"]) for e in events] == [("Synthetic bonus", "750.00")] or on > date.fromisoformat(forecast["horizon_end"])
    received = client.patch(f"{API}/expected-income/{made.json()['id']}", headers=OWNER, json={
        "label": "Synthetic bonus", "amount": "750", "currency": "AED", "expected_on": on.isoformat(), "received_on": today().isoformat()})
    assert received.json()["state"] == "received"
    assert client.get(f"{API}/expected-income", headers=OWNER).json()["total"] == 0
    assert client.get(f"{API}/expected-income", headers=OWNER, params={"state": "all"}).json()["total"] == 1
    assert client.delete(f"{API}/expected-income/{made.json()['id']}", headers=OWNER).status_code == 204
