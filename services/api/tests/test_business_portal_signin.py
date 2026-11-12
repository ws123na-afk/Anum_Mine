"""Signing in to the customer portal with an emailed code (docs/business-portal.md#signing-in-with-an-emailed-code)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest

from anum_api import notifications
from anum_api.business.store import Query, business_store
from anum_api.dependencies import memory_repository
from anum_api.notifications import MailOutcome
from anum_api.schemas import TenantContext, utc_now

from test_business import BASE, MEMBER, OTHER_TENANT, OWNER, TENANT, VIEWER, WORKSPACE, ok
from test_business_change_orders import project as make_project
from test_business_portal import ADMIN, PORTAL, bearer, contact_of, grant

from test_business import client
SIGN_IN = f"{PORTAL}/sign-in"
SCOPE = f"{TENANT}.{WORKSPACE}"
CONTEXT = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_biz", roles=["owner"])


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    business_store.clear()
    memory_repository.store.audit_records.clear()
    yield
    business_store.clear()


@pytest.fixture
def mail(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    sent: list[dict[str, str]] = []

    def send(to: str, subject: str, text: str, **_: Any) -> MailOutcome:
        sent.append({"to": to, "subject": subject, "text": text})
        return MailOutcome(status="sent", attempts=1, error=None)

    monkeypatch.setattr(notifications, "email_configured", lambda: True)
    monkeypatch.setattr(notifications, "send_with_retries", send)
    return sent


def turn_on() -> dict[str, Any]:
    return ok(client.put(f"{ADMIN}/sign-in", headers=OWNER, json={"enabled": True}))


def customer() -> tuple[dict[str, Any], str]:
    """A client account whose contact has portal access; returns the access and the contact's address."""
    account_id = make_project(total=1000, tax=0)["account_id"]
    contact = contact_of(account_id)
    assert contact["email"]
    return grant(account_id), contact["email"]


def ask(email: str, scope: str = SCOPE) -> Any:
    return client.post(f"{SIGN_IN}/code", json={"workspace": scope, "email": email})


def code_in(mail: list[dict[str, str]]) -> str:
    found = re.search(r"\b(\d{6})\b", mail[-1]["text"])
    assert found
    return found.group(1)


def verify(challenge: str, code: str, scope: str = SCOPE) -> Any:
    return client.post(f"{SIGN_IN}/verify", json={"workspace": scope, "challenge_id": challenge, "code": code})


def test_the_owner_turns_it_on_and_shares_the_sign_in_address(mail: list[dict[str, str]], monkeypatch: pytest.MonkeyPatch) -> None:
    view = ok(client.get(f"{ADMIN}/sign-in", headers=MEMBER))
    assert view["enabled"] is False and view["sign_in_path"] == f"/portal#signin={SCOPE}" and view["email_configured"] is True
    assert client.put(f"{ADMIN}/sign-in", headers=MEMBER, json={"enabled": True}).status_code == 403
    assert client.get(f"{ADMIN}/sign-in", headers=VIEWER).status_code in (200, 403)
    # Off: the sign-in page says so, for any address.
    assert ask("someone@example.com").status_code == 403
    assert turn_on()["enabled"] is True
    # The general settings route keeps the switch.
    ok(client.put(f"{BASE}/settings", headers=OWNER, json={"research_runs_per_day": 5, "research_searches_per_day": 10}))
    assert ok(client.get(f"{ADMIN}/sign-in", headers=OWNER))["enabled"] is True
    # Without a mail server it cannot be turned on.
    monkeypatch.setattr(notifications, "email_configured", lambda: False)
    ok(client.put(f"{ADMIN}/sign-in", headers=OWNER, json={"enabled": False}))
    refused = client.put(f"{ADMIN}/sign-in", headers=OWNER, json={"enabled": True})
    assert refused.status_code == 409 and "mail server" in refused.text
    assert any(r.action == "business.portal.code_sign_in_changed" for r in memory_repository.store.audit_records)


def test_a_customer_signs_in_with_a_code_and_reaches_only_their_portal(mail: list[dict[str, str]]) -> None:
    turn_on()
    access, email = customer()
    answer = ok(ask(email.upper()), 202)
    assert answer["expires_in_seconds"] == 600 and "If this address" in answer["message"]
    assert mail[-1]["to"] == email and "sign-in code" in mail[-1]["subject"]
    session = ok(verify(answer["challenge_id"], code_in(mail)))
    token = session["token"]
    assert token.startswith(f"anum_portal.{SCOPE}.bpsign_")
    me = ok(client.get(f"{PORTAL}/me", headers=bearer(token)))
    assert me["signed_in_with"] == "code" and me["contact_name"] == access["contact_name"]
    # The code works once.
    assert verify(answer["challenge_id"], code_in(mail)).status_code == 401
    # Only hashes are stored: never the address, the IP address or the code.
    [row] = business_store.scoped(CONTEXT).find("portal_sign_ins", Query(order_by="created_at", limit=5))[0]
    stored = row.model_dump_json()
    assert email not in stored and email.split("@")[0] not in stored and code_in(mail) not in stored and "testclient" not in stored
    # Signing out ends the session; an access link has nothing to sign out of.
    assert client.post(f"{SIGN_IN}/end", headers=bearer(token)).status_code == 204
    assert client.get(f"{PORTAL}/me", headers=bearer(token)).status_code == 401
    link = grant(access["account_id"])
    assert client.post(f"{SIGN_IN}/end", headers=bearer(link["token"])).status_code == 409
    actions = {r.action for r in memory_repository.store.audit_records}
    assert {"business.portal.sign_in_code_requested", "business.portal.signed_in_with_code", "business.portal.signed_out"} <= actions


def test_the_answer_never_says_whether_an_address_exists(mail: list[dict[str, str]]) -> None:
    turn_on()
    _, email = customer()
    known, unknown = ask(email), ask("nobody@unknown.example")
    assert known.status_code == unknown.status_code == 202
    assert known.json()["message"] == unknown.json()["message"] and set(known.json()) == set(unknown.json())
    assert len(mail) == 1  # nothing was sent to the unknown address
    # Any code for the unknown address is refused like a wrong code.
    first, second = verify(unknown.json()["challenge_id"], "123456"), verify(known.json()["challenge_id"], "000000")
    assert first.status_code == second.status_code == 401 and first.json()["error"]["message"] == second.json()["error"]["message"]
    # Another workspace's address does not reach this one's customers, and a malformed one is refused.
    assert ask(email, scope=f"{OTHER_TENANT['x-tenant-id']}.{OTHER_TENANT['x-workspace-id']}").status_code == 403
    assert ask(email, scope="no-dot").status_code == 422
    assert ask("not an address").status_code == 422


def test_five_wrong_codes_end_the_challenge_and_an_expired_code_fails(mail: list[dict[str, str]]) -> None:
    turn_on()
    _, email = customer()
    challenge = ok(ask(email), 202)["challenge_id"]
    code = code_in(mail)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert verify(challenge, wrong).status_code == 401
    assert verify(challenge, code).status_code == 401  # the right code no longer works
    challenge = ok(ask(email), 202)["challenge_id"]
    scoped = business_store.scoped(CONTEXT)
    row = scoped.get("portal_sign_ins", challenge)
    scoped.save("portal_sign_ins", row.model_copy(update={"expires_at": utc_now() - timedelta(seconds=1)}))
    assert verify(challenge, code_in(mail)).status_code == 401


def test_limits_per_address_and_per_ip_address(mail: list[dict[str, str]]) -> None:
    turn_on()
    _, email = customer()
    for _ in range(5):
        ok(ask(email), 202)
    assert ask(email).status_code == 429
    # Fifteen more addresses from the same IP address reach its limit of twenty an hour (refused requests do not count).
    for number in range(15):
        ok(ask(f"guess{number}@unknown.example"), 202)
    assert ask("other@unknown.example").status_code == 429


def test_turning_it_off_or_the_link_off_signs_the_customer_out(mail: list[dict[str, str]]) -> None:
    turn_on()
    access, email = customer()
    token = ok(verify(ok(ask(email), 202)["challenge_id"], code_in(mail)))["token"]
    assert client.get(f"{PORTAL}/invoices", headers=bearer(token)).status_code == 200
    ok(client.put(f"{ADMIN}/sign-in", headers=OWNER, json={"enabled": False}))
    assert client.get(f"{PORTAL}/me", headers=bearer(token)).status_code == 401
    turn_on()
    assert client.get(f"{PORTAL}/me", headers=bearer(token)).status_code == 200
    ok(client.post(f"{ADMIN}/access/{access['id']}/revoke", headers=OWNER))
    assert client.get(f"{PORTAL}/me", headers=bearer(token)).status_code == 401
    # No access left for the address: a new code is never sent.
    ok(ask(email), 202)
    assert len(mail) == 1
    # A forged session token is refused.
    forged = token.rsplit(".", 1)[0] + ".x" + "A" * 42
    assert client.get(f"{PORTAL}/me", headers=bearer(forged)).status_code == 401
