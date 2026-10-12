"""Sharing a report by email and WhatsApp (docs/reports.md#sharing): always a governed
tool call waiting for approval; nothing leaves before an owner approves the exact text.

Reuses the fake Gmail and Meta APIs of test_email.py and test_whatsapp.py.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator

import pytest
import test_email as mail
import test_whatsapp as wa
from test_email import fake  # noqa: F401  (fixture: fake mail providers)
from test_whatsapp import meta  # noqa: F401  (fixture: fake Meta Graph API)

from anum_api.reports import report_store


@pytest.fixture(autouse=True)
def _clean_reports() -> Iterator[None]:
    report_store.clear()
    yield
    report_store.clear()


def _report(client, headers) -> dict:  # type: ignore[no-untyped-def]
    body = {
        "type": "tyre", "title": "Tyre cost, September", "period_start": "2026-09-01", "period_end": "2026-09-30",
        "summary": "Costs rose on puncture repairs.",
        "metrics": [{"label": "Total cost", "value": "12,400", "unit": "SAR", "change_percent": 8.5}],
        "highlights": ["Puncture repairs doubled"], "data": {"columns": ["reason", "cost"], "rows": [["Puncture", 4200]]},
    }
    response = client.post("/api/v1/reports", headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_email_share_waits_for_approval_then_sends_exactly_the_report(fake: mail.FakeMail) -> None:  # noqa: F811
    account = mail.connect()
    report = _report(mail.client, mail.MEMBER)
    asked = mail.client.post(
        f"/api/v1/reports/{report['id']}/share/email",
        headers=mail.MEMBER,
        json={"account_id": account["id"], "to": ["Boss@Shop.example"], "note": "Here is September."},
    )
    assert asked.status_code == 200, asked.text
    result = asked.json()
    approval = result["approval"]
    assert result["status"] == "awaiting_approval" and approval["action"] == "send_email" and approval["risk_level"] == "high"
    assert approval["arguments"]["to"] == ["boss@shop.example"] and approval["arguments"]["in_reply_to"] is None
    assert "Total cost: 12,400 SAR (+8.5%)" in approval["arguments"]["body"]
    assert "| Puncture | 4200 |" in approval["arguments"]["body"]
    assert fake.gmail_sent == []
    assert mail.client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=mail.MEMBER,
                            json={"payload_hash": approval["payload_hash"]}).status_code == 403
    decided = mail.client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=mail.OWNER,
                               json={"payload_hash": approval["payload_hash"]})
    assert decided.status_code == 200, decided.text
    assert len(fake.gmail_sent) == 1
    raw = base64.urlsafe_b64decode(fake.gmail_sent[0]["raw"] + "==").decode()
    assert "Report: Tyre cost, September" in raw and "In-Reply-To" not in raw


def test_email_share_refusals(fake: mail.FakeMail) -> None:  # noqa: F811
    report = _report(mail.client, mail.OWNER)
    missing = mail.client.post(f"/api/v1/reports/{report['id']}/share/email", headers=mail.OWNER,
                               json={"account_id": "acct_missing", "to": ["a@shop.example"]})
    assert missing.status_code == 404 and "Connect one in Inbox" in missing.text
    account = mail.connect()
    bad = mail.client.post(f"/api/v1/reports/{report['id']}/share/email", headers=mail.OWNER,
                           json={"account_id": account["id"], "to": ["not-an-address"]})
    assert bad.status_code == 422
    viewer = mail.client.post(f"/api/v1/reports/{report['id']}/share/email", headers=mail.VIEWER,
                              json={"account_id": account["id"], "to": ["a@shop.example"]})
    assert viewer.status_code == 403
    assert fake.gmail_sent == []


def test_whatsapp_share_waits_for_approval_in_an_open_conversation(meta: wa.FakeMeta) -> None:  # noqa: F811
    wa.connect()
    wa.deliver(wa.inbound())
    conversation = wa.only_conversation()
    report = _report(wa.client, wa.MEMBER)
    asked = wa.client.post(f"/api/v1/reports/{report['id']}/share/whatsapp", headers=wa.MEMBER,
                           json={"conversation_id": conversation["id"], "note": "Your summary:"})
    assert asked.status_code == 200, asked.text
    result = asked.json()
    assert result["status"] == "awaiting_approval" and meta.sent == []
    approval = result["approval"]
    assert approval["action"] == "send_whatsapp" and approval["arguments"]["text"].startswith("Your summary:\nTyre cost, September")
    decided = wa.client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=wa.OWNER,
                             json={"payload_hash": approval["payload_hash"]})
    assert decided.status_code == 200, decided.text
    assert meta.sent[0]["text"]["body"] == approval["arguments"]["text"]


def test_whatsapp_share_outside_the_window_is_refused(meta: wa.FakeMeta) -> None:  # noqa: F811
    from datetime import timedelta

    wa.connect()
    wa.deliver(wa.inbound(age=timedelta(hours=30)))
    conversation = wa.only_conversation()
    report = _report(wa.client, wa.OWNER)
    refused = wa.client.post(f"/api/v1/reports/{report['id']}/share/whatsapp", headers=wa.OWNER,
                             json={"conversation_id": conversation["id"]})
    assert refused.status_code == 409 and meta.sent == []
