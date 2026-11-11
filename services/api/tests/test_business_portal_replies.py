"""Customers replying inside a portal request, and the team answering there (docs/business-portal.md#replying-inside-a-request).

Against the in-memory backend. A customer writes only in their own account's requests; a waiting request moves on and a
resolved one opens again; a closed one takes nothing. The team answers only while the customer has an active link, and
the answer is shown in the portal (nothing is sent anywhere). Isolation, permissions, idempotency, the daily limit,
cleaned text and the audit (ids only) are checked.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.business import portal as portal_module
from anum_api.business.store import business_store
from anum_api.dependencies import memory_repository
from anum_api.suppression import suppression_store

from test_business import BASE, MEMBER, OTHER_TENANT, OTHER_WORKSPACE, OWNER, VIEWER, ok
from test_business_change_orders import project as make_project
from test_business_portal import ADMIN, PORTAL, bearer, get, grant, sent_proposal

client = TestClient(main.app)
SUPPORT = f"{BASE}/support"


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    business_store.clear()
    suppression_store.clear()
    memory_repository.store.audit_records.clear()
    yield
    business_store.clear()
    suppression_store.clear()


def opened_request() -> tuple[dict[str, Any], str, dict[str, Any]]:
    project = make_project(total=1000, tax=0)
    access = grant(project["account_id"])
    ticket = ok(client.post(f"{PORTAL}/tickets", headers=bearer(access["token"]), json={"title": "Report is wrong", "description": "Totals differ."}), 201)
    return project, access["token"], ticket


def write(token: str, ticket_id: str, body: str, key: str | None = None) -> Any:
    return client.post(f"{PORTAL}/tickets/{ticket_id}/messages", headers=bearer(token), json={"body": body, "idempotency_key": key or uuid4().hex})


def status_to(ticket_id: str, to: str, **extra: Any) -> dict[str, Any]:
    return ok(client.post(f"{SUPPORT}/tickets/{ticket_id}/status", headers=MEMBER, json={"to": to, **extra}))


def test_a_customer_writes_and_the_team_answers_in_the_portal() -> None:
    _, token, ticket = opened_request()
    assert ticket["can_reply"] is True and ticket["messages"] == []
    sent = ok(write(token, ticket["id"], "The VAT line is missing on page 2."), 201)
    assert [(m["author"], m["body"]) for m in sent["messages"]] == [("you", "The VAT line is missing on page 2.")]
    desk = ok(client.get(f"{SUPPORT}/tickets", headers=MEMBER))["items"][0]
    assert desk["portal_unread"] == 1
    detail = ok(client.get(f"{SUPPORT}/tickets/{ticket['id']}", headers=MEMBER))
    assert detail["portal_open"] is True and detail["portal_messages"][0]["author"] == "customer" and detail["portal_messages"][0]["author_name"] == "Sara Khan"
    answered = ok(client.post(f"{SUPPORT}/tickets/{ticket['id']}/portal-messages", headers=MEMBER, json={"body": "Fixed, please check again."}), 201)
    assert answered["portal_unread"] == 0 and answered["first_responded_at"] is not None
    shown = get(token, f"/tickets/{ticket['id']}")["messages"]
    assert [m["author"] for m in shown] == ["you", "business"] and shown[1]["body"] == "Fixed, please check again."
    assert "member_biz" not in json.dumps(shown)  # no user id reaches the customer
    actions = [r.action for r in memory_repository.store.audit_records]
    assert "business.portal.ticket_message" in actions and "business.ticket.portal_answer" in actions
    assert all("VAT line" not in str(dict(r.metadata)) for r in memory_repository.store.audit_records)


def test_a_waiting_request_moves_on_a_resolved_one_reopens_and_a_closed_one_is_shut() -> None:
    _, token, ticket = opened_request()
    status_to(ticket["id"], "waiting_on_customer")
    ok(write(token, ticket["id"], "Here are the numbers you asked for."), 201)
    assert ok(client.get(f"{SUPPORT}/tickets/{ticket['id']}", headers=MEMBER))["status"] == "in_progress"
    status_to(ticket["id"], "resolved", resolution_code="fixed", resolution_summary="Totals corrected in the report.")
    ok(write(token, ticket["id"], "It is still wrong for March."), 201)
    reopened = ok(client.get(f"{SUPPORT}/tickets/{ticket['id']}", headers=MEMBER))
    assert reopened["status"] == "in_progress" and reopened["reopened_count"] == 1
    status_to(ticket["id"], "resolved", resolution_code="fixed", resolution_summary="March corrected as well.")
    status_to(ticket["id"], "closed")
    shut = write(token, ticket["id"], "One more thing.")
    assert shut.status_code == 409 and "Open a new request" in shut.text
    assert get(token, f"/tickets/{ticket['id']}")["can_reply"] is False
    staff = client.post(f"{SUPPORT}/tickets/{ticket['id']}/portal-messages", headers=MEMBER, json={"body": "Late answer"})
    assert staff.status_code == 409


def test_a_repeated_submit_is_one_message_and_text_is_cleaned() -> None:
    _, token, ticket = opened_request()
    key = uuid4().hex
    ok(write(token, ticket["id"], "Line one—with a dash\u0007\n\nLine two", key), 201)
    again = write(token, ticket["id"], "Line one—with a dash\u0007\n\nLine two", key)
    assert again.status_code == 200 and len(again.json()["messages"]) == 1
    assert again.json()["messages"][0]["body"] == "Line one, with a dash\n\nLine two"
    assert write(token, ticket["id"], "   ").status_code == 422
    bad_key = client.post(f"{PORTAL}/tickets/{ticket['id']}/messages", headers=bearer(token), json={"body": "Hi", "idempotency_key": "x"})
    assert bad_key.status_code == 422


def test_messages_per_link_are_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api.business import ticket_messages

    monkeypatch.setattr(ticket_messages, "MESSAGES_PER_DAY", 2)
    _, token, ticket = opened_request()
    ok(write(token, ticket["id"], "First"), 201)
    ok(write(token, ticket["id"], "Second"), 201)
    limited = write(token, ticket["id"], "Third")
    assert limited.status_code == 429 and "2 messages a day" in limited.text


def test_a_link_writes_only_in_its_own_accounts_requests() -> None:
    _, token, ticket = opened_request()
    other = sent_proposal(company="Desert Tyres")  # a second client
    their_token = grant(other["account_id"])["token"]
    assert write(their_token, ticket["id"], "Not mine").status_code == 404
    assert client.post(f"{PORTAL}/tickets/{ticket['id']}/messages", json={"body": "x", "idempotency_key": uuid4().hex}).status_code == 401
    assert client.post(f"{PORTAL}/tickets/btkt_missing/messages", headers=bearer(token), json={"body": "x", "idempotency_key": uuid4().hex}).status_code == 404


def test_the_team_answers_only_with_permission_scope_and_an_active_link() -> None:
    project, token, ticket = opened_request()
    url = f"{SUPPORT}/tickets/{ticket['id']}/portal-messages"
    assert client.post(url, headers=VIEWER, json={"body": "x"}).status_code == 403
    assert client.post(url, headers=OTHER_WORKSPACE, json={"body": "x"}).status_code == 404
    assert client.post(url, headers=OTHER_TENANT, json={"body": "x"}).status_code == 404
    # Revoke every link: nobody would read the answer.
    for item in ok(client.get(f"{ADMIN}/access?account_id={project['account_id']}", headers=OWNER)):
        ok(client.post(f"{ADMIN}/access/{item['id']}/revoke", headers=OWNER))
    refused = client.post(url, headers=MEMBER, json={"body": "Hello"})
    assert refused.status_code == 409 and "no active portal link" in refused.text
    assert ok(client.get(f"{SUPPORT}/tickets/{ticket['id']}", headers=MEMBER))["portal_open"] is False


def test_reading_marks_the_customer_messages() -> None:
    _, token, ticket = opened_request()
    ok(write(token, ticket["id"], "Hello"), 201)
    read = ok(client.post(f"{SUPPORT}/tickets/{ticket['id']}/portal-messages/read", headers=MEMBER))
    assert read["portal_unread"] == 0 and read["portal_messages"][0]["read_at"] is not None
    assert client.post(f"{SUPPORT}/tickets/{ticket['id']}/portal-messages/read", headers=VIEWER).status_code == 403


def test_an_archived_workspace_takes_no_message(monkeypatch: pytest.MonkeyPatch) -> None:
    _, token, ticket = opened_request()
    monkeypatch.setattr(portal_module, "workspace_archived", lambda context: True)
    refused = write(token, ticket["id"], "Hello")
    assert refused.status_code == 403 and "paused" in refused.text
    assert get(token, f"/tickets/{ticket['id']}")["can_reply"] is False
