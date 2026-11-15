"""Money lent or borrowed linked to a person in Contacts (docs/finance.md#people-from-contacts).

The loan keeps the typed name as history and the contact's id; the current name is read live from the
directory in the caller's scope. A foreign, missing or unreadable contact is refused; a renamed person shows the
new name; a deleted one leaves the loan with its typed name. Loans are grouped by person with their repayment
history. In-memory stores; every record is SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.contacts.store import memory_data as contacts_data
from anum_api.dependencies import memory_repository
from anum_api.finance import loan_contacts as LC
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.personal_store import personal_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.schemas import WorkspaceMembership

TENANT, WORKSPACE = "tenant_lc", "workspace_lc"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_lc", "x-user-roles": "owner"}
SECOND = {**OWNER, "x-user-id": "second_lc"}
ELSEWHERE = {**OWNER, "x-workspace-id": "workspace_lc_other"}
OTHER_TENANT = {**OWNER, "x-tenant-id": "tenant_lc_other"}
API = "/api/v1/finance"
STAMP = datetime(2026, 11, 1, tzinfo=UTC)
client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    stores = (finance_store, sms_memory_store, links_memory_store, personal_memory_store, contacts_data)
    for store in stores:
        store.clear()
    memberships = dict(memory_repository.store.memberships)
    for hdrs in (OWNER, SECOND, ELSEWHERE, OTHER_TENANT):
        key = (hdrs["x-tenant-id"], hdrs["x-workspace-id"], hdrs["x-user-id"])
        memory_repository.store.memberships[key] = WorkspaceMembership(
            tenant_id=key[0], workspace_id=key[1], user_id=key[2], role="owner", active=True, created_at=STAMP, updated_at=STAMP)
    yield
    memory_repository.store.memberships.clear()
    memory_repository.store.memberships.update(memberships)
    for store in stores:
        store.clear()


def ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.text
    return response.json() if response.content else None


def contact(name: str, hdrs: dict[str, str] = OWNER) -> str:
    return ok(client.post("/api/v1/contacts", headers=hdrs, json={"name": name}), 201)["person"]["id"]


def loan(person: str, amount: str = "500", contact_id: str | None = None, direction: str = "lent", lent_on: str = "2026-10-01",
         hdrs: dict[str, str] = OWNER, code: int = 201) -> Any:
    body: dict[str, Any] = {"direction": direction, "person": person, "amount": amount, "currency": "AED", "lent_on": lent_on}
    if contact_id is not None:
        body["contact_id"] = contact_id
    return ok(client.post(f"{API}/people-loans", headers=hdrs, json=body), code)


def put(loan_id: str, person: str, hdrs: dict[str, str] = OWNER, code: int = 200, **extra: Any) -> Any:
    body = {"direction": "lent", "person": person, "amount": "500", "currency": "AED", "lent_on": "2026-10-01", **extra}
    return ok(client.patch(f"{API}/people-loans/{loan_id}", headers=hdrs, json=body), code)


def test_a_loan_links_a_contact_and_shows_its_name() -> None:
    sara = contact("Sara Ahmed")
    made = loan("Sara", contact_id=sara)
    assert (made["contact_id"], made["contact_state"], made["contact_name"], made["person"]) == (sara, "linked", "Sara Ahmed", "Sara")
    detail = ok(client.get(f"{API}/people-loans/{made['id']}", headers=OWNER))
    assert detail["contact_name"] == "Sara Ahmed"
    # The list finds the loan by the contact's name as well as the typed one.
    assert ok(client.get(f"{API}/people-loans", headers=OWNER, params={"q": "ahmed"}))["total"] == 1
    # A manual name stays the fallback: no contact, no contact fields.
    manual = loan("Cousin Omar")
    assert (manual["contact_id"], manual["contact_state"], manual["contact_name"]) == (None, None, None)


def test_a_rename_in_contacts_shows_the_new_name_and_keeps_the_typed_one() -> None:
    sara = contact("Sara Ahmed")
    made = loan("Sara", contact_id=sara)
    ok(client.patch(f"/api/v1/contacts/{sara}", headers=OWNER, json={"name": "Sara Al Mansoori"}))
    seen = ok(client.get(f"{API}/people-loans", headers=OWNER))["items"][0]
    assert (seen["contact_id"], seen["contact_name"], seen["person"]) == (sara, "Sara Al Mansoori", "Sara")
    # Editing the loan without sending contact_id keeps the link.
    edited = put(made["id"], "Sara A.")
    assert (edited["contact_id"], edited["contact_name"], edited["person"]) == (sara, "Sara Al Mansoori", "Sara A.")


def test_a_deleted_contact_leaves_the_loan_with_its_typed_name() -> None:
    sara = contact("Sara Ahmed")
    made = loan("Sara", contact_id=sara)
    ok(client.delete(f"/api/v1/contacts/{sara}", headers=OWNER), 204)
    seen = ok(client.get(f"{API}/people-loans/{made['id']}", headers=OWNER))
    assert (seen["contact_id"], seen["contact_state"], seen["contact_name"], seen["person"]) == (sara, "deleted", None, "Sara")
    # The kept link never blocks an edit; picking a deleted person again is refused; clearing it works.
    assert put(made["id"], "Sara", notes="paid half")["contact_state"] == "deleted"
    assert "not in Contacts" in ok(client.post(f"{API}/people-loans", headers=OWNER, json={
        "direction": "lent", "person": "Sara", "amount": "1", "currency": "AED", "lent_on": "2026-10-01", "contact_id": sara}), 422)["error"]["message"]
    cleared = put(made["id"], "Sara", contact_id=None)
    assert (cleared["contact_id"], cleared["contact_state"]) == (None, None)


def test_a_contact_from_another_workspace_or_tenant_or_unknown_is_refused() -> None:
    elsewhere = contact("Hidden Person", ELSEWHERE)
    foreign = contact("Foreign Person", OTHER_TENANT)
    for contact_id in (elsewhere, foreign, "contact_missing"):
        refused = loan("X", contact_id=contact_id, code=422)
        assert refused["error"]["message"] == LC.CONTACT_MISSING
    made = loan("X")
    put(made["id"], "X", contact_id=foreign, code=422)
    assert ok(client.get(f"{API}/people-loans/{made['id']}", headers=OWNER))["contact_id"] is None


def test_a_caller_who_cannot_read_contacts_cannot_link_and_sees_no_name(monkeypatch: pytest.MonkeyPatch) -> None:
    sara = contact("Sara Ahmed")
    made = loan("Sara", contact_id=sara)
    monkeypatch.setattr(LC, "can_read", lambda context: False)
    refused = loan("Sara", contact_id=sara, code=403)
    assert refused["error"]["message"] == LC.CONTACT_UNREADABLE
    seen = ok(client.get(f"{API}/people-loans/{made['id']}", headers=OWNER))
    assert (seen["contact_state"], seen["contact_name"], seen["person"]) == ("hidden", None, "Sara")


def test_loans_are_private_and_the_contact_link_does_not_leak_between_people() -> None:
    sara = contact("Sara Ahmed")
    loan("Sara", contact_id=sara)
    # Another person in the same workspace may read the same contact but never sees the owner's loans.
    assert ok(client.get(f"{API}/people-loans/people", headers=SECOND))["total"] == 0
    assert ok(client.get(f"{API}/people-loans", headers=SECOND))["total"] == 0
    assert ok(client.get(f"{API}/people-loans/people", headers=ELSEWHERE))["total"] == 0


def test_several_loans_are_grouped_under_the_contact_with_their_history() -> None:
    sara = contact("Sara Ahmed")
    first = loan("Sara", "500", contact_id=sara, lent_on="2026-09-01")
    second = loan("Sara A.", "300", contact_id=sara, direction="borrowed", lent_on="2026-10-05")
    loan("cousin  omar", "100", lent_on="2026-08-01")
    loan("Cousin Omar", "50", lent_on="2026-08-10")
    # A repayment linked to the first loan shows in the person's history.
    account = ok(client.post(f"{API}/accounts", headers=OWNER, json={"name": "Current", "kind": "bank", "currency": "AED", "opening_balance": "0"}), 201)
    paid = ok(client.post(f"{API}/transactions", headers=OWNER, json={
        "account_id": account["id"], "posted_on": "2026-10-10", "amount": "200", "description": "Sara repaid", "category": "income"}), 201)
    entries = ok(client.get(f"{API}/ledger/entries", headers=OWNER))["items"]
    canonical = next(e["canonical_id"] for e in entries if e["source_ref"] == paid["id"])
    ok(client.post(f"{API}/people-loans/{first['id']}/links", headers=OWNER, json={"canonical_id": canonical, "kind": "repayment", "amount": "200"}), 201)

    page = ok(client.get(f"{API}/people-loans/people", headers=OWNER))
    assert page["total"] == 2
    groups = {g["key"]: g for g in page["items"]}
    group = groups[f"contact:{sara}"]
    assert (group["name"], group["names"], group["loans"], group["open_loans"]) == ("Sara Ahmed", ["Sara A.", "Sara"], 2, 2)
    assert group["owed"] == [{"currency": "AED", "they_owe_me": "300.00", "i_owe": "300.00", "they_owe_me_overdue": "0.00", "i_owe_overdue": "0.00"}]
    assert groups["name:cousin omar"]["loans"] == 2
    assert ok(client.get(f"{API}/people-loans/people", headers=OWNER, params={"q": "omar"}))["total"] == 1

    detail = ok(client.get(f"{API}/people-loans/people/detail", headers=OWNER, params={"key": f"contact:{sara}"}))
    assert [i["id"] for i in detail["items"]] == [second["id"], first["id"]]
    assert [(h["loan_id"], h["movement"]["link"]["amount"]) for h in detail["history"]] == [(first["id"], "200.00")]
    ok(client.get(f"{API}/people-loans/people/detail", headers=OWNER, params={"key": "contact:nobody"}), 404)
    # Renaming the contact renames the group; the typed names stay.
    ok(client.patch(f"/api/v1/contacts/{sara}", headers=OWNER, json={"name": "Sara M."}))
    again = ok(client.get(f"{API}/people-loans/people/detail", headers=OWNER, params={"key": f"contact:{sara}"}))
    assert (again["name"], again["names"]) == ("Sara M.", ["Sara A.", "Sara"])
