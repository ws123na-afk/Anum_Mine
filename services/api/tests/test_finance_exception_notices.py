"""Exception notices of the daily job (docs/finance.md#exception-notices). In-memory stores.

Each kind is off until the owner turns it on; every item is noticed at most once a day (claimed, then notified, released
when sending fails); notices are in-app only. Payments, vendors and documents are SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from test_business_books import (  # noqa: F401  (``clean`` is the autouse fixture that resets every store)
    FIN,
    PERSONAL,
    PERSONAL_OWNER,
    TENANT,
    bill,
    clean,
    client,
    setup_books,
    today,
    vendor,
)

from anum_api.finance import exception_notices as N
from anum_api.finance import suggestion_jobs as J
from anum_api.finance.rules_store import rules_memory_store
from anum_api.schemas import TenantContext, utc_now
from anum_api.team_store import memory_team_store

CTX = TenantContext(tenant_id=TENANT, workspace_id=PERSONAL, user_id="owner_books")


@pytest.fixture(autouse=True)
def rules() -> Iterator[None]:
    rules_memory_store.clear()
    yield
    rules_memory_store.clear()


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def fake(context: TenantContext, title: str, body: str) -> bool:
        calls.append((title, body))
        return True

    monkeypatch.setattr(N, "send", fake)
    return calls


def policy(**body: Any) -> None:
    response = client.put(f"{FIN}/automation", headers=PERSONAL_OWNER, json=body)
    assert response.status_code == 200, response.text


def uncategorized(amount: str = "-600.00") -> None:
    bank = client.post(f"{FIN}/accounts", headers=PERSONAL_OWNER, json={"name": "Current", "kind": "bank", "currency": "SAR"}).json()
    made = client.post(f"{FIN}/transactions", headers=PERSONAL_OWNER, json={"account_id": bank["id"], "amount": amount,
                                                                            "description": "QUOKKA UNKNOWN 77", "posted_on": today().isoformat()})
    assert made.status_code == 201, made.text


def run(now: Any = None) -> J.SuggestionJobResult:
    return J.run_person(CTX.tenant_id, CTX.workspace_id, CTX.user_id, now=now)


def test_nothing_is_noticed_until_the_owner_turns_a_kind_on(sent: list) -> None:
    uncategorized()
    assert run().exception_notices == 0 and sent == []
    policy(notify_unresolved=False, notices=[])
    assert run().exception_notices == 0 and sent == []
    assert client.put(f"{FIN}/automation", headers=PERSONAL_OWNER, json={"notices": ["email_everyone"]}).status_code == 422


def test_uncategorized_money_above_the_threshold_is_noticed_once_a_day(sent: list) -> None:
    uncategorized("-600.00")
    policy(notices=["uncategorized_money"], notice_uncategorized_min="1000")
    assert run().exception_notices == 0  # below the owner's threshold
    policy(notices=["uncategorized_money"], notice_uncategorized_min="500")
    assert run().exception_notices == 1
    assert "SAR 600.00" in sent[0][1]
    assert run().exception_notices == 0 and len(sent) == 1  # the same item, the same day: once
    assert run(utc_now() + timedelta(days=1)).exception_notices == 1  # a new day


def test_a_failed_notice_releases_its_claims_for_the_next_run(monkeypatch: pytest.MonkeyPatch) -> None:
    uncategorized()
    policy(notices=["uncategorized_money"], notice_uncategorized_min="100")
    monkeypatch.setattr(N, "send", lambda *args: False)
    result = run()
    assert result.exception_notices == 0 and result.failed == 1
    monkeypatch.setattr(N, "send", lambda *args: True)
    assert run().exception_notices == 1


def test_the_notice_is_in_app_only() -> None:
    from anum_api.push import IN_APP_ONLY_KINDS

    uncategorized()
    policy(notices=["uncategorized_money"], notice_uncategorized_min="100")
    assert run().exception_notices == 1
    found = [n for n in memory_team_store.list_notifications(CTX, limit=50, before=None, unread_only=False) if n.kind == N.NOTICE_KIND]
    assert len(found) == 1 and found[0].email_status.value == "not_requested" and found[0].push_status.value == "not_requested"
    assert N.NOTICE_KIND in IN_APP_ONLY_KINDS


def test_business_evidence_missing_and_unposted_documents_reach_the_owner(sent: list) -> None:
    setup_books(evidence_threshold="500")
    landlord = vendor()
    made = bill(landlord["id"])
    assert made.status_code == 201, made.text

    policy(notices=["evidence_missing"])
    assert run().exception_notices == 1
    assert "above its evidence threshold" in sent[-1][1] and sent[-1][1].startswith("1 bill")
    assert run().exception_notices == 0
    # Only owners and accountants of the business are told: a person who is a plain member is not.
    member = TenantContext(tenant_id=TENANT, workspace_id=PERSONAL, user_id="member_books")
    assert N.businesses(member) == []
    assert [name for _ctx, name in N.businesses(CTX)]
    # A bill in another currency without a rate cannot be posted: an unposted document of the business.
    assert bill(landlord["id"], description="Synthetic import", currency="USD", subtotal="20", tax="0").status_code == 201
    policy(notices=["business_unposted"])
    assert run().exception_notices == 1
    assert sent[-1][0] == "Business documents are not posted" and "not in its journal" in sent[-1][1]
    assert run().exception_notices == 0


def test_statement_months_missing_are_noticed_from_the_reconciliation_health_check(sent: list) -> None:
    bank = client.post(f"{FIN}/accounts", headers=PERSONAL_OWNER, json={"name": "Current", "kind": "bank", "currency": "SAR"}).json()
    earlier = (today().replace(day=1) - timedelta(days=40)).replace(day=10)
    made = client.post(f"{FIN}/transactions", headers=PERSONAL_OWNER, json={"account_id": bank["id"], "amount": "-50.00", "category": "groceries",
                                                                            "description": "EXAMPLE GROCER", "posted_on": earlier.isoformat()})
    assert made.status_code == 201, made.text
    policy(notices=["statement_missing", "reconciliation_overdue"], notice_exception_days=7)
    assert run().exception_notices == 1
    assert sent[0][0] == "Statements are missing"
    assert run().exception_notices == 0
