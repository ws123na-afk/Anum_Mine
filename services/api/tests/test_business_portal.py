"""The customer portal (docs/business-portal.md), against the in-memory backend.

Covers the access model (links shown once, hashed, expiring, revocable, never a workspace
role), isolation between client accounts, workspaces and tenants, what a customer sees (no
internal figure ever), approving or declining an offer or a change order bound to the exact
version, invoices and their printable copy, support requests, archived workspaces and the
audit walk.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.business import portal as portal_module
from anum_api.business.invoice_models import PortalAccessStatus
from anum_api.business.models import ReplyChannel, ReplyStatus, TicketReply
from anum_api.business.portal_auth import PREFIX, mint, parse, secret_hash
from anum_api.business.store import business_store
from anum_api.schemas import new_id, utc_now
from anum_api.suppression import suppression_store

from test_business import BASE, MEMBER, OTHER_TENANT, OTHER_WORKSPACE, OWNER, TENANT, VIEWER, WORKSPACE, approve, lead, ok, proposal_body, qualify
from test_business_change_orders import decide, draft as draft_change, project as make_project, submit as submit_change
from test_business_invoices import milestone_ids, send, set_profile

client = TestClient(main.app)
PORTAL = "/api/v1/portal"
ADMIN = f"{BASE}/portal"


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    business_store.clear()
    suppression_store.clear()
    yield
    business_store.clear()
    suppression_store.clear()


def contact_of(account_id: str) -> dict[str, Any]:
    return ok(client.get(f"{BASE}/accounts/{account_id}", headers=OWNER))["contacts"][0]


def grant(account_id: str, headers: dict[str, str] = OWNER, **body: Any) -> dict[str, Any]:
    return ok(client.post(f"{ADMIN}/access", headers=headers, json={"account_id": account_id, "contact_id": contact_of(account_id)["id"], **body}), 201)


def bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def get(token: str, path: str, expected: int = 200) -> Any:
    response = client.get(f"{PORTAL}{path}", headers=bearer(token))
    assert response.status_code == expected, response.text
    return response.json() if response.headers["content-type"].startswith("application/json") else response.text


def decision(token: str, path: str, content_hash: str, verdict: str = "approved", name: str = "Sara Khan", **extra: Any) -> Any:
    return client.post(f"{PORTAL}{path}/decision", headers=bearer(token), json={"decision": verdict, "content_hash": content_hash, "signer_name": name, **extra})


def sent_proposal(total: float = 2500, company: str | None = None, **content: Any) -> dict[str, Any]:
    """A proposal waiting for acceptance (sent from the owner's side), for a second client when ``company`` is given."""
    slug = (company or "Gulf Mixers").split()[0].lower()
    created = lead(company=company, website=f"https://{slug}.example", email=f"ops@{slug}.example") if company else lead()
    qualify(created["lead"]["id"])
    opportunity = ok(client.post(f"{BASE}/opportunities", headers=MEMBER, json={"lead_id": created["lead"]["id"]}), 201)
    body = proposal_body(opportunity["id"], total)
    body["content"].update(content)
    proposal = ok(client.post(f"{BASE}/proposals", headers=MEMBER, json=body), 201)
    return ok(client.post(f"{BASE}/proposals/{proposal['id']}/request-acceptance", headers=MEMBER))


# -- the link ------------------------------------------------------------------------------------------


def test_a_link_is_minted_hashed_and_parsed() -> None:
    token, hashed = mint("tenant_a", "workspace_a", "bpacc_1")
    assert parse(token) is not None and parse(token)[:3] == ("tenant_a", "workspace_a", "bpacc_1")  # type: ignore[index]
    assert token.startswith(PREFIX + ".") and secret_hash(parse(token)[3]) == hashed and parse(token)[3] not in hashed  # type: ignore[index]
    assert mint("tenant_a", "workspace_a", "bpacc_1")[0] != token
    for bad in ("", "x", f"{PREFIX}.a.b.c", f"{PREFIX}.a.b.c.short", f"{PREFIX}.a.b.c." + "x" * 200, "other.a.b.c." + "x" * 30, f"{PREFIX}.a b.b.c." + "x" * 30):
        assert parse(bad) is None
    with pytest.raises(ValueError):
        mint("tenant.a", "w", "a")


def test_only_an_owner_gives_access_and_the_link_is_shown_once() -> None:
    project = make_project(total=1000, tax=0)
    account_id = project["account_id"]
    contact_id = contact_of(account_id)["id"]
    assert client.post(f"{ADMIN}/access", headers=MEMBER, json={"account_id": account_id, "contact_id": contact_id}).status_code == 403
    assert client.get(f"{ADMIN}/access?account_id={account_id}", headers=VIEWER).status_code == 403
    created = grant(account_id)
    assert created["token"].startswith(PREFIX + ".") and created["portal_path"] == f"/portal#access={created['token']}" and created["status"] == "active"
    listed = ok(client.get(f"{ADMIN}/access?account_id={account_id}", headers=MEMBER))
    assert len(listed) == 1 and "token" not in listed[0] and "token_hash" not in listed[0] and listed[0]["contact_name"] == "Sara Khan" and listed[0]["last_used_at"] is None
    stored = business_store._rows["portal_access"][created["id"]]
    assert stored.token_hash == secret_hash(created["token"].split(".")[4]) and created["token"] not in json.dumps(stored.model_dump(mode="json"))
    assert client.post(f"{ADMIN}/access", headers=OWNER, json={"account_id": account_id, "contact_id": contact_id, "expires_in_days": 0}).status_code == 422
    assert client.post(f"{ADMIN}/access", headers=OWNER, json={"account_id": account_id, "contact_id": contact_id, "expires_in_days": 400}).status_code == 422
    other = lead(company="Other Co", website="https://other.example", email="a@other.example")
    assert client.post(f"{ADMIN}/access", headers=OWNER, json={"account_id": account_id, "contact_id": contact_of(other["account"]["id"])["id"]}).status_code == 422
    assert client.post(f"{ADMIN}/access", headers=OWNER, json={"account_id": "missing", "contact_id": contact_id}).status_code == 404


def test_a_do_not_contact_contact_gets_no_link_and_loses_the_one_they_had() -> None:
    project = make_project(total=1000, tax=0)
    account_id = project["account_id"]
    token = grant(account_id)["token"]
    get(token, "/me")
    ok(client.post(f"{BASE}/contacts/{contact_of(account_id)['id']}/do-not-contact", headers=OWNER, json={"on": True, "reason": "Asked us not to"}))
    assert client.get(f"{PORTAL}/me", headers=bearer(token)).status_code == 401
    assert client.post(f"{ADMIN}/access", headers=OWNER, json={"account_id": account_id, "contact_id": contact_of(account_id)["id"]}).status_code == 409


def test_a_workspace_has_a_limit_of_active_links_per_account() -> None:
    project = make_project(total=1000, tax=0)
    for _ in range(10):
        grant(project["account_id"])
    assert client.post(f"{ADMIN}/access", headers=OWNER, json={"account_id": project["account_id"], "contact_id": contact_of(project["account_id"])["id"]}).status_code == 409


def test_bad_expired_revoked_and_foreign_links_all_fail_closed() -> None:
    project = make_project(total=1000, tax=0)
    created = grant(project["account_id"])
    token = created["token"]
    assert client.get(f"{PORTAL}/me").status_code == 401
    assert client.get(f"{PORTAL}/me", headers={"authorization": "Bearer nonsense"}).status_code == 401
    assert client.get(f"{PORTAL}/me", headers={"authorization": f"Basic {token}"}).status_code == 401
    # The owner's own headers are not a portal credential.
    assert client.get(f"{PORTAL}/me", headers=OWNER).status_code == 401
    tenant, workspace, access_id, secret = parse(token)  # type: ignore[misc]
    wrong_secret = f"{PREFIX}.{tenant}.{workspace}.{access_id}.{'A' * len(secret)}"
    unknown = f"{PREFIX}.{tenant}.{workspace}.bpacc_unknown.{secret}"
    other_tenant = f"{PREFIX}.tenant_biz_other.workspace_biz_b.{access_id}.{secret}"
    other_workspace = f"{PREFIX}.{tenant}.workspace_biz_other.{access_id}.{secret}"
    messages = {client.get(f"{PORTAL}/me", headers=bearer(value)).json()["error"]["message"] for value in (wrong_secret, unknown, other_tenant, other_workspace)}
    assert len(messages) == 1  # the same answer whatever was wrong
    for value in (wrong_secret, unknown, other_tenant, other_workspace):
        assert client.get(f"{PORTAL}/me", headers=bearer(value)).status_code == 401
    # A portal link is not a workspace credential either.
    assert client.get(f"{BASE}/invoices", headers=bearer(token)).status_code == 401
    # Expiry.
    business_store._rows["portal_access"][access_id].expires_at = utc_now() - timedelta(seconds=1)
    expired = client.get(f"{PORTAL}/me", headers=bearer(token))
    assert expired.status_code == 401 and "expired" in expired.text
    business_store._rows["portal_access"][access_id].expires_at = utc_now() + timedelta(days=1)
    get(token, "/me")
    # Revocation by an owner only, and idempotent.
    assert client.post(f"{ADMIN}/access/{access_id}/revoke", headers=MEMBER).status_code == 403
    revoked = ok(client.post(f"{ADMIN}/access/{access_id}/revoke", headers=OWNER))
    assert revoked["status"] == "revoked" and revoked["revoked_at"]
    assert ok(client.post(f"{ADMIN}/access/{access_id}/revoke", headers=OWNER))["status"] == "revoked"
    turned_off = client.get(f"{PORTAL}/me", headers=bearer(token))
    assert turned_off.status_code == 401 and "turned off" in turned_off.text
    assert client.post(f"{ADMIN}/access/missing/revoke", headers=OWNER).status_code == 404
    assert business_store._rows["portal_access"][access_id].status == PortalAccessStatus.REVOKED


def test_use_is_recorded_without_a_write_on_every_request() -> None:
    project = make_project(total=1000, tax=0)
    created = grant(project["account_id"])
    get(created["token"], "/me")
    first = business_store._rows["portal_access"][created["id"]].last_used_at
    assert first is not None
    get(created["token"], "/me")
    assert business_store._rows["portal_access"][created["id"]].last_used_at == first
    assert ok(client.get(f"{ADMIN}/access?account_id={project['account_id']}", headers=OWNER))[0]["last_used_at"]


# -- the home page ----------------------------------------------------------------------------------------


def test_portal_answers_are_never_cached() -> None:
    project = make_project(total=1000, tax=0)
    token = grant(project["account_id"])["token"]
    for path in ("/me", "/offers", "/invoices", "/tickets"):
        response = client.get(f"{PORTAL}{path}", headers=bearer(token))
        assert response.headers["cache-control"] == "no-store" and response.headers["referrer-policy"] == "no-referrer"


def test_me_names_the_customer_and_counts_what_needs_them() -> None:
    set_profile()
    project = make_project(total=2000, tax=0)
    token = grant(project["account_id"])["token"]
    me = get(token, "/me")
    assert (me["business_name"], me["account_name"], me["contact_name"], me["can_decide"]) == ("Anum Technologies LLC", "Gulf Mixers LLC", "Sara Khan", True)
    assert me["counts"] == {"offers_waiting": 0, "change_orders_waiting": 0, "invoices_open": 0, "invoices_overdue": 0, "tickets_open": 0}
    assert [item["name"] for item in me["projects"]] == [project["name"]]
    send(ok(client.post(f"{BASE}/invoices", headers=MEMBER, json={"project_id": project["id"], "milestone_ids": milestone_ids(project)[:1]}), 201))
    assert get(token, "/me")["counts"]["invoices_open"] == 1
    assert "tenant" not in json.dumps(me).lower() and "workspace" not in json.dumps(me).lower()


# -- offers ---------------------------------------------------------------------------------------------------


def test_an_offer_is_visible_only_once_sent_and_shows_no_internal_figure() -> None:
    created = lead()
    qualify(created["lead"]["id"])
    opportunity = ok(client.post(f"{BASE}/opportunities", headers=MEMBER, json={"lead_id": created["lead"]["id"]}), 201)
    draft = ok(client.post(f"{BASE}/proposals", headers=MEMBER, json=proposal_body(opportunity["id"], 2500)), 201)
    token = grant(created["account"]["id"])["token"]
    assert get(token, "/offers") == []
    get(token, f"/offers/{draft['id']}", 404)
    pending = ok(client.post(f"{BASE}/proposals/{draft['id']}/request-acceptance", headers=MEMBER))
    offers = get(token, "/offers")
    assert [(item["id"], item["status"], item["can_decide"], item["total"]) for item in offers] == [(draft["id"], "waiting", True, 2500)]
    detail = get(token, f"/offers/{draft['id']}")
    assert detail["content_hash"] == pending["requested_hash"] and detail["line_items"][0]["amount"] == 2500 and len(detail["milestones"]) == 2
    text = json.dumps(detail).lower()
    for secret in ("internal_cost", "margin", "created_by", "approval", "tenant_id", "workspace_id", "opportunity"):
        assert secret not in text
    assert get(token, "/me")["counts"]["offers_waiting"] == 1


def test_approving_an_offer_records_a_decision_for_the_owner_who_still_confirms() -> None:
    pending = sent_proposal()
    token = grant(pending["account_id"])["token"]
    detail = get(token, f"/offers/{pending['id']}")
    assert decision(token, f"/offers/{pending['id']}", "f" * 64).status_code == 409  # not the version shown
    assert decision(token, f"/offers/{pending['id']}", detail["content_hash"], name="S").status_code == 422
    assert decision(token, f"/offers/{pending['id']}", detail["content_hash"], name="    ").status_code == 422
    approved = decision(token, f"/offers/{pending['id']}", detail["content_hash"], note="Looks good, go ahead")
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["decision"]["decision"] == "approved" and body["decision"]["signer_name"] == "Sara Khan" and body["can_decide"] is False and body["status"] == "waiting"
    # Repeating is harmless; reversing is refused.
    assert decision(token, f"/offers/{pending['id']}", detail["content_hash"]).status_code == 200
    reversed_ = decision(token, f"/offers/{pending['id']}", detail["content_hash"], verdict="declined")
    assert reversed_.status_code == 409 and "already approved" in reversed_.text
    assert len(business_store._rows["portal_decisions"]) == 1
    assert get(token, "/me")["counts"]["offers_waiting"] == 0
    # The owner sees it on the proposal and still decides in Approvals; the proposal is not accepted by the customer alone.
    seen = ok(client.get(f"{BASE}/proposals/{pending['id']}", headers=OWNER))
    assert seen["status"] == "pending_approval" and seen["portal_decision"]["decision"] == "approved" and seen["portal_decision"]["current"] is True
    assert seen["portal_decision"]["signer_name"] == "Sara Khan" and seen["portal_decision"]["note"] == "Looks good, go ahead"
    rows = ok(client.get(f"{ADMIN}/decisions?subject_id={pending['id']}", headers=MEMBER))
    assert len(rows) == 1 and rows[0]["contact_name"] == "Sara Khan"
    assert approve(pending["approval"]).status_code == 200
    accepted = get(token, f"/offers/{pending['id']}")
    assert accepted["status"] == "accepted" and accepted["can_decide"] is False
    assert decision(token, f"/offers/{pending['id']}", accepted["content_hash"]).status_code == 409


def test_a_declined_offer_is_recorded_and_the_owner_is_told() -> None:
    pending = sent_proposal()
    token = grant(pending["account_id"])["token"]
    shown = get(token, f"/offers/{pending['id']}")["content_hash"]
    assert decision(token, f"/offers/{pending['id']}", shown, verdict="declined", note="Too expensive").status_code == 200
    seen = ok(client.get(f"{BASE}/proposals/{pending['id']}", headers=OWNER))
    assert seen["portal_decision"]["decision"] == "declined" and seen["portal_decision"]["note"] == "Too expensive"
    kinds = [item.kind for item in business_store._rows["activities"].values()]
    assert "portal_proposal_declined" in kinds


def test_a_read_only_link_cannot_decide() -> None:
    pending = sent_proposal()
    token = grant(pending["account_id"], can_decide=False)["token"]
    detail = get(token, f"/offers/{pending['id']}")
    assert detail["can_decide"] is False
    refused = decision(token, f"/offers/{pending['id']}", detail["content_hash"])
    assert refused.status_code == 403 and "can read but not approve" in refused.text
    assert business_store._rows["portal_decisions"] == {}


def test_an_offer_past_its_validity_cannot_be_approved() -> None:
    pending = sent_proposal(valid_until=(utc_now() - timedelta(days=1)).date().isoformat())
    token = grant(pending["account_id"])["token"]
    detail = get(token, f"/offers/{pending['id']}")
    assert detail["expired"] is True and detail["can_decide"] is False
    assert decision(token, f"/offers/{pending['id']}", detail["content_hash"]).status_code == 409


def test_a_changed_offer_cannot_be_approved_with_the_old_hash() -> None:
    pending = sent_proposal()
    token = grant(pending["account_id"])["token"]
    old = get(token, f"/offers/{pending['id']}")["content_hash"]
    changed = proposal_body("x", 3000)["content"]
    ok(client.put(f"{BASE}/proposals/{pending['id']}/content", headers=MEMBER, json=changed))
    # Back to a draft: no longer shown.
    get(token, f"/offers/{pending['id']}", 404)
    assert decision(token, f"/offers/{pending['id']}", old).status_code == 404


# -- isolation between accounts, workspaces and tenants ---------------------------------------------------------


def test_a_link_reaches_only_its_own_account() -> None:
    set_profile()
    mine = make_project(total=2000, tax=0)
    mine_token = grant(mine["account_id"])["token"]
    send(ok(client.post(f"{BASE}/invoices", headers=MEMBER, json={"project_id": mine["id"], "milestone_ids": milestone_ids(mine)[:1]}), 201))
    pending = sent_proposal(company="Desert Tyres")  # a second client's offer
    theirs = pending["account_id"]
    their_token = grant(theirs)["token"]
    assert [item["id"] for item in get(their_token, "/offers")] == [pending["id"]]
    assert pending["id"] not in [item["id"] for item in get(mine_token, "/offers")]
    get(mine_token, f"/offers/{pending['id']}", 404)
    assert decision(mine_token, f"/offers/{pending['id']}", get(their_token, f"/offers/{pending['id']}")["content_hash"]).status_code == 404
    their_invoice = get(mine_token, "/invoices")[0]
    get(their_token, f"/invoices/{their_invoice['id']}", 404)
    get(their_token, f"/invoices/{their_invoice['id']}/print", 404)
    assert get(their_token, "/invoices") == []
    ticket = ok(client.post(f"{PORTAL}/tickets", headers=bearer(mine_token), json={"title": "Dashboard is slow", "description": "It takes a minute to load."}), 201)
    get(their_token, f"/tickets/{ticket['id']}", 404)
    assert get(their_token, "/tickets") == []
    # The customer cannot name a project of another account for a request.
    other_project = client.post(f"{PORTAL}/tickets", headers=bearer(their_token), json={"title": "Not mine", "description": "A project that is not ours.", "project_id": mine["id"]})
    assert other_project.status_code == 404


def test_another_workspace_or_tenant_sees_nothing_through_its_own_headers() -> None:
    set_profile()
    project = make_project(total=2000, tax=0)
    grant(project["account_id"])
    for headers in (OTHER_WORKSPACE, OTHER_TENANT):
        assert ok(client.get(f"{BASE}/accounts", headers=headers))["total"] == 0
        assert client.get(f"{ADMIN}/access?account_id={project['account_id']}", headers=headers).status_code == 404
        assert client.post(f"{ADMIN}/access", headers=headers, json={"account_id": project["account_id"], "contact_id": "x"}).status_code == 404
        assert ok(client.get(f"{ADMIN}/decisions", headers=headers)) == []


# -- change orders -------------------------------------------------------------------------------------------------


def test_a_customer_approves_a_change_order_and_the_owner_still_decides() -> None:
    project = make_project(total=2500, tax=0)
    token = grant(project["account_id"])["token"]
    change = draft_change(project["id"])
    assert get(token, "/change-orders") == []  # a draft is not shown
    submitted = submit_change(change["id"])
    listed = get(token, "/change-orders")
    assert [(item["number"], item["status"], item["can_decide"], item["total_delta"]) for item in listed] == [(1, "waiting", True, 800)]
    detail = get(token, f"/change-orders/{change['id']}")
    assert detail["content_hash"] == submitted["content_hash"] and detail["lines"][0]["action"] == "add" and detail["timeline_days"] == 10
    assert "internal_cost" not in json.dumps(detail) and "created_by" not in json.dumps(detail)
    assert decision(token, f"/change-orders/{change['id']}", "0" * 64).status_code == 409
    approved = decision(token, f"/change-orders/{change['id']}", detail["content_hash"])
    assert approved.status_code == 200 and approved.json()["decision"]["decision"] == "approved" and approved.json()["can_decide"] is False
    seen = ok(client.get(f"{BASE}/change-orders/{change['id']}", headers=OWNER))
    assert seen["status"] == "pending_approval" and seen["portal_decision"]["decision"] == "approved" and seen["portal_decision"]["current"] is True
    assert decide(submitted).status_code == 200
    final = get(token, f"/change-orders/{change['id']}")
    assert final["status"] == "approved" and final["can_decide"] is False
    assert decision(token, f"/change-orders/{change['id']}", detail["content_hash"]).status_code == 409


def test_a_stale_change_order_cannot_be_approved_and_a_rejected_one_disappears() -> None:
    project = make_project(total=2500, tax=0)
    token = grant(project["account_id"])["token"]
    first = submit_change(draft_change(project["id"])["id"])
    second = submit_change(draft_change(project["id"], title="Another")["id"])
    assert decide(first).status_code == 200  # the contract moves on: the second is now stale
    stale = get(token, f"/change-orders/{second['id']}")
    assert stale["stale"] is True and stale["can_decide"] is False
    refused = decision(token, f"/change-orders/{second['id']}", stale["content_hash"])
    assert refused.status_code == 409 and "sent again" in refused.text
    third = submit_change(draft_change(project["id"], title="Third")["id"])
    ok(client.post(f"{BASE}/change-orders/{third['id']}/reject", headers=OWNER, json={"reason": "Not now"}))
    get(token, f"/change-orders/{third['id']}", 404)


# -- invoices ------------------------------------------------------------------------------------------------------


def test_a_customer_sees_sent_invoices_with_payments_and_a_printable_copy() -> None:
    set_profile()
    project = make_project(total=2000, tax=15)
    token = grant(project["account_id"])["token"]
    drafted = ok(client.post(f"{BASE}/invoices", headers=MEMBER, json={"project_id": project["id"], "milestone_ids": milestone_ids(project)[:1], "notes": "Pay within 14 days"}), 201)
    assert get(token, "/invoices") == []
    get(token, f"/invoices/{drafted['id']}", 404)
    pending = ok(client.post(f"{BASE}/invoices/{drafted['id']}/request-approval", headers=MEMBER))
    assert get(token, "/invoices") == []  # waiting for approval: still not shown
    assert approve(pending["approval"]).status_code == 200
    listed = get(token, "/invoices")
    assert len(listed) == 1 and listed[0]["status"] == "sent" and listed[0]["payment_state"] == "unpaid" and listed[0]["balance"] == listed[0]["total"]
    detail = get(token, f"/invoices/{drafted['id']}")
    assert detail["seller"]["legal_name"] == "Anum Technologies LLC" and "IBAN" in detail["seller"]["bank_details"] and detail["bill_to_name"] == "Gulf Mixers LLC"
    assert detail["lines"][0]["total"] == detail["total"] and detail["notes"] == "Pay within 14 days"
    text = json.dumps(detail)
    for secret in ("created_by", "approval", "tenant_id", "marked_milestones", "content_hash", "decision_note", "requested"):
        assert secret not in text
    ok(client.post(f"{BASE}/invoices/{drafted['id']}/payments", headers=MEMBER, json={"amount": 100, "paid_on": utc_now().date().isoformat(), "reference": "TRX-5"}), 201)
    paid = get(token, f"/invoices/{drafted['id']}")
    assert paid["amount_paid"] == 100 and paid["payment_state"] == "partially_paid" and paid["payments"][0]["reference"] == "TRX-5"
    page = get(token, f"/invoices/{drafted['id']}/print?lang=ar")
    assert 'dir="rtl"' in page and detail["number"] in page
    assert get(token, "/me")["counts"]["invoices_open"] == 1
    ok(client.post(f"{BASE}/invoices/{drafted['id']}/payments", headers=MEMBER, json={"amount": float(detail["total"]) - 100, "paid_on": utc_now().date().isoformat()}), 201)
    assert get(token, f"/invoices/{drafted['id']}")["status"] == "paid" and get(token, "/me")["counts"]["invoices_open"] == 0
    assert [item["id"] for item in get(token, "/invoices?status=paid")] == [drafted["id"]] and get(token, "/invoices?status=sent") == []


def test_a_voided_invoice_stays_visible_marked_void() -> None:
    set_profile()
    project = make_project(total=2000, tax=0)
    token = grant(project["account_id"])["token"]
    sent = send(ok(client.post(f"{BASE}/invoices", headers=MEMBER, json={"project_id": project["id"], "milestone_ids": milestone_ids(project)[:1]}), 201))
    ok(client.post(f"{BASE}/invoices/{sent['id']}/void", headers=OWNER, json={"reason": "Wrong amount"}))
    voided = get(token, f"/invoices/{sent['id']}")
    assert voided["status"] == "void" and voided["void_reason"] == "Wrong amount" and voided["payment_state"] == "unpaid"
    assert get(token, "/me")["counts"]["invoices_open"] == 0


# -- reporting a payment, credit notes and the PDF -------------------------------------------------------------


def report(token: str, invoice_id: str, amount: float = 400, **extra: Any) -> Any:
    body = {"payer_name": "Sara Khan", "amount": amount, "paid_on": utc_now().date().isoformat(), "method": "bank_transfer", "reference": "TRX-31", **extra}
    return client.post(f"{PORTAL}/invoices/{invoice_id}/payment-notices", headers=bearer(token), json=body)


def sent_for_portal(total: float = 2000, tax: float = 0) -> tuple[dict[str, Any], dict[str, Any], str]:
    set_profile()
    project = make_project(total=total, tax=tax)
    invoice = send(ok(client.post(f"{BASE}/invoices", headers=MEMBER, json={"project_id": project["id"], "milestone_ids": milestone_ids(project)[:1]}), 201))
    return project, invoice, grant(project["account_id"])["token"]


def test_a_customer_reports_a_payment_and_nothing_changes_until_the_business_confirms() -> None:
    _, invoice, token = sent_for_portal()
    before = get(token, f"/invoices/{invoice['id']}")
    assert before["can_report_payment"] is True and before["payment_notices"] == [] and before["credit_notes"] == []
    created = report(token, invoice["id"], 400)
    assert created.status_code == 201, created.text
    detail = created.json()
    assert (detail["amount_paid"], detail["balance"]) == (0, invoice["total"])
    notice = detail["payment_notices"][0]
    assert (notice["amount"], notice["status"], notice["reference"], notice["method"]) == (400, "pending", "TRX-31", "bank_transfer")
    assert "payer_name" not in json.dumps(detail) and "access_id" not in json.dumps(detail) and "decided_by" not in json.dumps(detail)
    # The business sees it on the invoice and in the account's activity.
    owner_view = ok(client.get(f"{BASE}/invoices/{invoice['id']}", headers=OWNER))
    assert owner_view["payment_notices"][0]["payer_name"] == "Sara Khan" and owner_view["payment_notices"][0]["status"] == "pending" and owner_view["amount_paid"] == 0
    assert "portal_payment_reported" in [item.kind for item in business_store._rows["activities"].values()]
    # Confirming records an ordinary payment, which the customer then sees.
    ok(client.post(f"{BASE}/invoices/{invoice['id']}/payment-notices/{notice['id']}/confirm", headers=MEMBER, json={}))
    after = get(token, f"/invoices/{invoice['id']}")
    assert after["amount_paid"] == 400 and after["payments"][0]["reference"] == "TRX-31" and after["payment_notices"][0]["status"] == "confirmed"
    assert after["payment_notices"][0]["decision_note"] == ""


def test_a_rejected_report_shows_the_businesss_reason_and_can_be_made_again() -> None:
    _, invoice, token = sent_for_portal()
    notice = report(token, invoice["id"], 400).json()["payment_notices"][0]
    ok(client.post(f"{BASE}/invoices/{invoice['id']}/payment-notices/{notice['id']}/reject", headers=OWNER, json={"reason": "Nothing arrived on that date"}))
    shown = get(token, f"/invoices/{invoice['id']}")["payment_notices"][0]
    assert shown["status"] == "rejected" and shown["decision_note"] == "Nothing arrived on that date"
    assert report(token, invoice["id"], 400).status_code == 201  # a rejected report no longer counts as waiting


def test_reporting_the_same_payment_twice_is_harmless() -> None:
    _, invoice, token = sent_for_portal()
    assert report(token, invoice["id"], 400).status_code == 201
    again = report(token, invoice["id"], 400)
    assert again.status_code == 200 and len(again.json()["payment_notices"]) == 1


def test_a_report_is_checked_against_the_invoice() -> None:
    _, invoice, token = sent_for_portal()
    total = invoice["total"]
    assert report(token, invoice["id"], 0).status_code == 422 and report(token, invoice["id"], -5).status_code == 422
    over = report(token, invoice["id"], total + 0.01)
    assert over.status_code == 422 and "balance" in over.text
    assert report(token, invoice["id"], 10, paid_on="2999-01-01").status_code == 422
    assert report(token, invoice["id"], 10, paid_on="2000-01-01").status_code == 422
    assert report(token, invoice["id"], 10, payer_name=" a ").status_code == 422
    assert report(token, invoice["id"], 10, method="bitcoin").status_code == 422
    assert client.post(f"{PORTAL}/invoices/{invoice['id']}/payment-notices", headers=bearer(token), json={"amount": 10}).status_code == 422
    # Reports that together would exceed the balance, and too many waiting at once, are refused.
    assert report(token, invoice["id"], total - 100, reference="A").status_code == 201
    crowded = report(token, invoice["id"], 200, reference="B")
    assert crowded.status_code == 409 and "cover this balance" in crowded.text
    for index in range(4):
        assert report(token, invoice["id"], 10, reference=f"C{index}").status_code == 201
    capped = report(token, invoice["id"], 1, reference="D")
    assert capped.status_code == 409 and "already reported" in capped.text
    assert len(ok(client.get(f"{BASE}/invoices/{invoice['id']}/payment-notices", headers=OWNER))) == 5
    assert ok(client.get(f"{BASE}/invoices/{invoice['id']}", headers=OWNER))["amount_paid"] == 0


def test_a_customer_text_is_cleaned_and_never_executes() -> None:
    _, invoice, token = sent_for_portal()
    created = report(token, invoice["id"], 10, payer_name="  Sara   Khan \u2013 Ltd ", reference="TRX \u2014 <script>alert(1)</script>", note="paid  today").json()
    stored = ok(client.get(f"{BASE}/invoices/{invoice['id']}/payment-notices", headers=OWNER))[0]
    assert stored["payer_name"] == "Sara Khan, Ltd"
    assert "\u2014" not in stored["reference"] and "\u2014" not in created["payment_notices"][0]["reference"] and stored["note"] == "paid today"


def test_a_read_only_link_cannot_report_a_payment() -> None:
    project, invoice, _ = sent_for_portal()
    token = grant(project["account_id"], can_decide=False)["token"]
    detail = get(token, f"/invoices/{invoice['id']}")
    assert detail["can_report_payment"] is False
    refused = report(token, invoice["id"], 10)
    assert refused.status_code == 403 and "can read but not act" in refused.text
    assert business_store._rows["payment_notices"] == {}


def test_a_payment_cannot_be_reported_on_a_paid_void_or_foreign_invoice() -> None:
    project, invoice, token = sent_for_portal()
    other = sent_proposal(company="Desert Tyres")
    other_token = grant(other["account_id"])["token"]
    assert report(other_token, invoice["id"], 10).status_code == 404  # another client's invoice looks absent
    assert report(token, "missing", 10).status_code == 404
    ok(client.post(f"{BASE}/invoices/{invoice['id']}/payments", headers=MEMBER, json={"amount": invoice["total"], "paid_on": utc_now().date().isoformat()}), 201)
    paid = report(token, invoice["id"], 10)
    assert paid.status_code == 409 and "already paid" in paid.text
    assert get(token, f"/invoices/{invoice['id']}")["can_report_payment"] is False
    second = send(ok(client.post(f"{BASE}/invoices", headers=MEMBER, json={"project_id": project["id"], "milestone_ids": milestone_ids(project)[1:2]}), 201))
    ok(client.post(f"{BASE}/invoices/{second['id']}/void", headers=OWNER, json={"reason": "Wrong amount"}))
    voided = report(token, second["id"], 10)
    assert voided.status_code == 409 and "cancelled" in voided.text
    # A draft is not visible to the customer at all.
    draft = ok(client.post(f"{BASE}/invoices", headers=MEMBER, json={"account_id": project["account_id"], "lines": [{"description": "Work", "unit_price": 50}]}), 201)
    assert report(token, draft["id"], 10).status_code == 404


def test_an_archived_workspace_takes_no_payment_report(monkeypatch: pytest.MonkeyPatch) -> None:
    _, invoice, token = sent_for_portal()
    monkeypatch.setattr(portal_module, "workspace_archived", lambda context: True)
    refused = report(token, invoice["id"], 10)
    assert refused.status_code == 403 and "paused" in refused.text
    assert get(token, f"/invoices/{invoice['id']}")["can_report_payment"] is False


def test_the_customer_sees_credit_notes_and_the_lower_balance() -> None:
    _, invoice, token = sent_for_portal(total=2000, tax=15)
    ok(client.post(f"{BASE}/invoices/{invoice['id']}/credit-notes", headers=OWNER, json={"amount": 100, "reason": "Agreed discount"}), 201)
    detail = get(token, f"/invoices/{invoice['id']}")
    assert detail["amount_credited"] == 100 and detail["balance"] == invoice["total"] - 100 and detail["payment_state"] == "unpaid"
    note = detail["credit_notes"][0]
    assert note["number"] == f"{invoice['number']}-CN1" and note["amount"] == 100 and note["reason"] == "Agreed discount" and note["status"] == "issued"
    assert "issued_by" not in json.dumps(detail) and "subtotal" not in json.dumps(detail["credit_notes"])
    listed = get(token, "/invoices")[0]
    assert listed["amount_credited"] == 100 and listed["balance"] == invoice["total"] - 100
    assert "Agreed discount" in get(token, f"/invoices/{invoice['id']}/print")
    ok(client.post(f"{BASE}/invoices/{invoice['id']}/credit-notes", headers=OWNER, json={"amount": invoice["total"] - 100, "reason": "Rest waived"}), 201)
    settled = get(token, f"/invoices/{invoice['id']}")
    assert settled["status"] == "paid" and settled["payment_state"] == "credited" and settled["can_report_payment"] is False
    assert get(token, "/me")["counts"]["invoices_open"] == 0


def test_the_customer_downloads_a_pdf_of_their_own_invoice_only() -> None:
    from io import BytesIO

    from pypdf import PdfReader

    project, invoice, token = sent_for_portal()
    response = client.get(f"{PORTAL}/invoices/{invoice['id']}/pdf", headers=bearer(token))
    assert response.status_code == 200 and response.headers["content-type"] == "application/pdf" and response.content.startswith(b"%PDF")
    assert response.headers["cache-control"] == "no-store" and response.headers["content-disposition"] == f'attachment; filename="{invoice["number"]}.pdf"'
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
    assert invoice["number"] in text and "Anum Technologies LLC" in text
    draft = ok(client.post(f"{BASE}/invoices", headers=MEMBER, json={"account_id": project["account_id"], "lines": [{"description": "Work", "unit_price": 50}]}), 201)
    assert client.get(f"{PORTAL}/invoices/{draft['id']}/pdf", headers=bearer(token)).status_code == 404
    other_token = grant(sent_proposal(company="Desert Tyres")["account_id"])["token"]
    assert client.get(f"{PORTAL}/invoices/{invoice['id']}/pdf", headers=bearer(other_token)).status_code == 404
    assert client.get(f"{PORTAL}/invoices/{invoice['id']}/pdf").status_code == 401


# -- support requests ----------------------------------------------------------------------------------------------


def test_a_customer_opens_a_request_that_reaches_the_support_desk() -> None:
    project = make_project(total=1000, tax=0)
    token = grant(project["account_id"])["token"]
    assert get(token, "/tickets") == []
    assert client.post(f"{PORTAL}/tickets", headers=bearer(token), json={"title": "Hi", "description": "x"}).status_code == 422
    assert client.post(f"{PORTAL}/tickets", headers=bearer(token), json={"title": "    ab", "description": "Something is wrong"}).status_code == 422
    assert client.post(f"{PORTAL}/tickets", headers=bearer(token), json={"title": "Report is wrong", "description": "Totals differ.", "severity": "critical"}).status_code == 422
    opened = ok(client.post(f"{PORTAL}/tickets", headers=bearer(token), json={"title": "Report is wrong", "description": "Totals differ from our books.", "severity": "high",
                                                                            "project_id": project["id"]}), 201)
    assert opened["status"] == "new" and opened["opened_here"] is True and opened["project_name"] == project["name"] and opened["reference"].startswith("T-")
    assert "assignee" not in json.dumps(opened) and "sla" not in json.dumps(opened).lower() and "entitlement" not in json.dumps(opened)
    desk = ok(client.get(f"{BASE}/support/tickets", headers=MEMBER))
    assert desk["total"] == 1 and desk["items"][0]["account_id"] == project["account_id"] and desk["items"][0]["requester_name"] == "Sara Khan" and desk["items"][0]["requester_verified"]
    assert desk["items"][0]["severity"] == "high"
    assert get(token, "/me")["counts"]["tickets_open"] == 1
    detail = get(token, f"/tickets/{opened['id']}")
    assert detail["description"] == "Totals differ from our books." and detail["replies"] == []
    # Only a reply that was sent shows; a draft is the owner's.
    now = utc_now()
    for status_, body in ((ReplyStatus.DRAFT, "Draft not for the customer"), (ReplyStatus.SENT, "We have fixed the totals.")):
        reply = TicketReply(id=new_id("btrp"), tenant_id=TENANT, workspace_id=WORKSPACE, ticket_id=opened["id"], channel=ReplyChannel.EMAIL, body=body, status=status_,
                            sent_at=now if status_ == ReplyStatus.SENT else None, created_by="owner_biz", created_at=now, updated_at=now)
        business_store._rows["ticket_replies"][reply.id] = reply
    shown = get(token, f"/tickets/{opened['id']}")["replies"]
    assert [item["body"] for item in shown] == ["We have fixed the totals."]


def test_requests_per_link_are_limited() -> None:
    project = make_project(total=1000, tax=0)
    token = grant(project["account_id"])["token"]
    for index in range(5):
        ok(client.post(f"{PORTAL}/tickets", headers=bearer(token), json={"title": f"Question {index}", "description": "Something to ask."}), 201)
    limited = client.post(f"{PORTAL}/tickets", headers=bearer(token), json={"title": "One more", "description": "Something to ask."})
    assert limited.status_code == 429 and "5 requests a day" in limited.text
    other = grant(project["account_id"])["token"]  # each link has its own allowance
    assert client.post(f"{PORTAL}/tickets", headers=bearer(other), json={"title": "One more", "description": "Something to ask."}).status_code == 201


# -- archived workspaces ---------------------------------------------------------------------------------------------


def test_an_archived_workspace_is_read_only_in_the_portal(monkeypatch: pytest.MonkeyPatch) -> None:
    pending = sent_proposal()
    token = grant(pending["account_id"])["token"]
    shown = get(token, f"/offers/{pending['id']}")["content_hash"]
    monkeypatch.setattr(portal_module, "workspace_archived", lambda context: True)
    refused = decision(token, f"/offers/{pending['id']}", shown)
    assert refused.status_code == 403 and "paused" in refused.text
    assert client.post(f"{PORTAL}/tickets", headers=bearer(token), json={"title": "Question", "description": "Something to ask."}).status_code == 403
    assert get(token, "/offers")  # still readable


# -- audit walk -----------------------------------------------------------------------------------------------------------


def test_every_state_changing_portal_route_is_audited_without_content() -> None:
    from audit_probe import AuditProbe

    set_profile()
    pending = sent_proposal(company="Desert Tyres")
    project = make_project(total=2500, tax=0)
    change = submit_change(draft_change(project["id"])["id"])
    admin_probe = AuditProbe(client, "business_portal")
    created = admin_probe.call("POST", f"{ADMIN}/access", OWNER, json={"account_id": pending["account_id"], "contact_id": contact_of(pending["account_id"])["id"]}).json()
    spare = admin_probe.call("POST", f"{ADMIN}/access", OWNER, json={"account_id": project["account_id"], "contact_id": contact_of(project["account_id"])["id"]}).json()
    token, other_token = created["token"], spare["token"]
    admin_probe.call("POST", f"{ADMIN}/access/{created['id']}/revoke", OWNER)
    fresh = grant(pending["account_id"])["token"]
    assert client.get(f"{PORTAL}/me", headers=bearer(token)).status_code == 401
    scope = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE}
    note, name = "Please go ahead with the work", "Sara Khan"
    portal_probe = AuditProbe(client, "portal", secrets=[fresh, other_token])
    shown = get(fresh, f"/offers/{pending['id']}")["content_hash"]
    portal_probe.call("POST", f"{PORTAL}/offers/{pending['id']}/decision", {**bearer(fresh), **scope}, sent=[note, name],
                json={"decision": "approved", "content_hash": shown, "signer_name": name, "note": note})
    portal_probe.call("POST", f"{PORTAL}/change-orders/{change['id']}/decision", {**bearer(other_token), **scope}, sent=[note, name],
                json={"decision": "declined", "content_hash": change["content_hash"], "signer_name": name, "note": note})
    portal_probe.call("POST", f"{PORTAL}/tickets", {**bearer(fresh), **scope}, sent=["Dashboard is slow", "It takes a minute to load the page."],
                json={"title": "Dashboard is slow", "description": "It takes a minute to load the page."})
    admin_probe.assert_covered()
    set_profile()
    billed = send(ok(client.post(f"{BASE}/invoices", headers=MEMBER, json={"project_id": project["id"], "milestone_ids": milestone_ids(project)[:1]}), 201))
    reporter = grant(project["account_id"])["token"]
    portal_probe.call("POST", f"{PORTAL}/invoices/{billed['id']}/payment-notices", {**bearer(reporter), **scope}, sent=["TRX-AUDIT-31", "Sara Khan", "paid by wire"],
                      json={"payer_name": "Sara Khan", "amount": 10, "paid_on": utc_now().date().isoformat(), "reference": "TRX-AUDIT-31", "note": "paid by wire"})
    portal_probe.assert_covered()
