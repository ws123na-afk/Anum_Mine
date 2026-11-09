"""Money borrowed from a person is a debt; money lent is a receivable (docs/finance.md#lent-and-borrowed).

``obligations.borrowed_debts`` turns each open loan borrowed from a person into a debt that every reader
shares: the payoff order, Safe to spend (a minimum only once its due date is in this month or has passed)
and Fixed costs. A person also saved as a Debt in the same currency is that Debt, never counted twice.
Every record is SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.dependencies import memory_repository
from anum_api.finance import obligations as O
from anum_api.finance import personal as P
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.models import Debt, DebtKind, PlanSettings
from anum_api.finance.personal_store import personal_memory_store
from anum_api.finance.safe_to_spend import compute
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.schemas import WorkspaceMembership

TENANT, WORKSPACE = "tenant_pd", "workspace_pd"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_pd", "x-user-roles": "owner"}
API = "/api/v1/finance"
STAMP = datetime(2026, 10, 1, tzinfo=UTC)
TODAY = date(2026, 10, 14)
client = TestClient(main.app)


def owned(**values: object) -> dict:
    return {"tenant_id": TENANT, "workspace_id": WORKSPACE, "owner_user_id": "owner_pd", "created_at": STAMP, "updated_at": STAMP, **values}


def loan(id: str, direction: str, person: str, amount: str, due_on: date | None = None, currency: str = "AED", closed: bool = False) -> P.PersonLoan:
    return P.PersonLoan(**owned(id=id, direction=P.LoanDirection(direction), person=person, amount=D(amount), currency=currency,
                                lent_on=date(2026, 8, 1), due_on=due_on, closed_on=date(2026, 9, 1) if closed else None))


class Repaid:
    """A live repayment link, as ``obligations.load`` returns them."""

    def __init__(self, target_id: str, amount: str, role: str = "payment", currency: str = "AED") -> None:
        self.target_id, self.amount, self.role, self.currency, self.status = target_id, D(amount), role, currency, "active"


def test_open_borrowed_loans_are_debts_with_a_minimum_only_when_due() -> None:
    loans = [
        loan("later", "borrowed", "Cousin A", "900", date(2026, 12, 1)),  # due after this month: no minimum
        loan("soon", "borrowed", "Cousin B", "400", date(2026, 10, 25)),  # due this month
        loan("late", "borrowed", "Cousin C", "300", date(2026, 9, 1)),  # overdue since last month
        loan("open", "borrowed", "Cousin D", "200"),  # no date
        loan("done", "borrowed", "Cousin E", "100", date(2026, 9, 1)),  # repaid in full
        loan("gone", "borrowed", "Cousin F", "100", closed=True),  # closed by the person
        loan("lent", "lent", "Friend G", "700"),  # they owe me: a receivable, not a debt
    ]
    links = [Repaid("soon", "150"), Repaid("done", "100")]
    found = {d.id: d for d in O.borrowed_debts(loans, links, [], TODAY)}
    assert set(found) == {"later", "soon", "late", "open"}
    assert (found["soon"].balance, found["soon"].minimum_payment, found["soon"].due_day, found["soon"].overdue) == (D("250"), D("250"), 25, False)
    assert (found["late"].minimum_payment, found["late"].due_day, found["late"].overdue) == (D("300"), None, True)
    assert found["later"].minimum_payment == 0 and found["open"].minimum_payment == 0
    assert all(d.apr_percent == 0 and d.kind == DebtKind.OTHER and d.source == "person_loan" for d in found.values())
    assert O.lent_receivables(loans, links, TODAY) == {"AED": D("700")}


def test_a_person_also_saved_as_a_debt_is_counted_once() -> None:
    saved = [Debt(**owned(id="d1", name="cousin-a ", kind=DebtKind.LOAN, balance=D("900"), currency="AED"))]
    assert O.borrowed_debts([loan("x", "borrowed", "Cousin A", "900")], [], saved, TODAY) == []
    # Another currency is another debt.
    assert len(O.borrowed_debts([loan("x", "borrowed", "Cousin A", "900", currency="SAR")], [], saved, TODAY)) == 1


def test_safe_to_spend_and_fixed_costs_count_a_borrowed_loan_when_it_is_due() -> None:
    debts = O.borrowed_debts([loan("soon", "borrowed", "Cousin B", "400", date(2026, 10, 25)),
                              loan("late", "borrowed", "Cousin C", "300", date(2026, 9, 1)),
                              loan("open", "borrowed", "Cousin D", "200")], [], [], TODAY)
    result = compute(today=TODAY, base_currency="AED", timezone="Asia/Dubai", accounts=[], balances={}, bills=[], debts=debts, goals=[],
                     plan=PlanSettings(), rates=[])
    lines = {line.ref_id: (line.note, line.amount, line.params) for line in result.currencies[0].lines}
    assert lines == {"soon": ("minimum_due", D("-400"), {"day": "25"}), "late": ("minimum_overdue", D("-300"), {"date": "2026-09-01"})}
    counted, _skipped = P.fixed_commitments([], debts, [], [], TODAY)
    assert [(c.name, c.monthly, c.source, c.note) for c in counted] == [("Cousin B", D("400"), "person_loan", "due_this_month"),
                                                                       ("Cousin C", D("300"), "person_loan", "due_this_month")]


@pytest.fixture()
def owner() -> Iterator[None]:
    for store in (finance_store, sms_memory_store, links_memory_store, personal_memory_store):
        store.clear()
    memory_repository.store.memberships[(TENANT, WORKSPACE, "owner_pd")] = WorkspaceMembership(
        tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_pd", role="owner", active=True, created_at=STAMP, updated_at=STAMP)
    yield
    memory_repository.store.memberships.pop((TENANT, WORKSPACE, "owner_pd"), None)
    for store in (finance_store, sms_memory_store, links_memory_store, personal_memory_store):
        store.clear()


def test_borrowed_money_joins_the_payoff_plan_and_lent_money_is_not_a_debt(owner: None) -> None:
    assert client.post(f"{API}/debts", headers=OWNER, json={"name": "Card A", "balance": "1200", "apr_percent": "12", "minimum_payment": "100",
                                                         "currency": "AED"}).status_code == 201
    for direction, person in (("borrowed", "Cousin B"), ("lent", "Friend C")):
        made = client.post(f"{API}/people-loans", headers=OWNER, json={"direction": direction, "person": person, "amount": "500", "currency": "AED",
                                                                       "lent_on": "2026-08-01"})
        assert made.status_code == 201, made.text
    row = client.get(f"{API}/planner/payoff", headers=OWNER, params={"payment": "300"}).json()["currencies"][0]
    assert {(d["name"], d["source"], d["apr_percent"]) for d in row["debts"]} == {("Card A", "debt", "12.0000"), ("Cousin B", "person_loan", "0")}
    # No rate is guessed for a loan with a person: it is interest free, so it goes last in avalanche order.
    names = {d["id"]: d["name"] for d in row["debts"]}
    assert [names[i] for i in row["avalanche"]["order"]] == ["Card A", "Cousin B"]
    summary = client.get(f"{API}/summary", headers=OWNER).json()
    assert summary["debts"] == [{"currency": "AED", "amount": "1700.00"}]


def test_net_worth_counts_people_installments_and_holdings_at_cost() -> None:
    from anum_api.finance.models import AccountKind, Holding, InstallmentStatementEntry
    from anum_api.finance.summary import AccountBalance, net_worth

    accounts = [AccountBalance(id="bank", name="Bank", kind=AccountKind.BANK, currency="AED", institution=None, last4=None, balance=D("5000.00"), archived=False),
                AccountBalance(id="broker", name="Broker", kind=AccountKind.INVESTMENT, currency="AED", institution=None, last4=None, balance=D("3000.00"),
                               archived=False)]
    holdings = [Holding(**owned(id="h1", account_id="broker", symbol="AAA", quantity=D("10"), cost_basis=D("2800"), currency="AED")),
                Holding(**owned(id="h2", account_id="broker", symbol="BBB", quantity=D("5"), cost_basis=D("0"), currency="AED"))]
    links = O.Links(person_loans=[loan("lent", "lent", "Friend G", "700"), loan("borrowed", "borrowed", "Cousin A", "400")])
    plan = [InstallmentStatementEntry(provider="Tamara", order_id="example-order", merchant="Example Shop", due_on=date(2026, 11, 1), installment=2,
                                      total_installments=4, amount=D("250"), currency="AED", source_status="scheduled"),
            InstallmentStatementEntry(provider="Tamara", order_id="example-order", merchant="Example Shop", due_on=date(2026, 9, 1), installment=1,
                                      total_installments=4, amount=D("250"), currency="AED", source_status="paid")]
    parts, excluded = net_worth(accounts, {"AED": D("400")}, holdings, links, plan, TODAY)
    aed = parts[0]
    # 5,000 in the bank, 2,800 of holdings at cost instead of the broker's ledger balance, 700 lent; less 400 borrowed and 250 still due.
    assert (aed.accounts, aed.holdings_at_cost, aed.receivables, aed.debts, aed.installments, aed.net) == (
        D("5000.00"), D("2800.00"), D("700.00"), D("400.00"), D("250.00"), D("7850.00"))
    assert [(e.name, e.reason) for e in excluded] == [("BBB", "no_cost_basis")]
