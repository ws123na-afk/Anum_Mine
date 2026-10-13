"""Contact suppression (opt-out) list (docs/privacy.md#contact-suppression)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import email_inbox, main
from anum_api.agent_tools import ToolCall, ToolDefinition, ToolPolicyOutcome, ToolRegistry, ToolResult
from anum_api.email_inbox import EmailMessage
from anum_api.model_gateway import MockModelGateway
from anum_api.repository import InMemoryRepository
from anum_api.runtime import AgentRuntime
from anum_api.schemas import RiskLevel, TenantContext, utc_now
from anum_api.store import InMemoryStore, store
from anum_api.suppression import (
    SuppressionChannel,
    apply_inbound_keyword,
    keyword_of,
    mask,
    normalize,
    recipient_hash,
    suppressed_for_call,
    suppression_store,
)

from test_whatsapp import BASE, CUSTOMER, MEMBER, FakeMeta, connect, deliver, inbound, meta, only_conversation  # noqa: F401

TENANT = "tenant_wa"
WORKSPACE = "workspace_wa"
OTHER_TENANT = "tenant_supp_other"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_wa", "x-user-roles": "owner"}
ORG_ADMIN_MEMBER = {**OWNER, "x-user-id": "admin_wa", "x-user-roles": "member,org_admin"}
VIEWER = {**OWNER, "x-user-id": "viewer_wa", "x-user-roles": "viewer"}
OTHER_OWNER = {**OWNER, "x-tenant-id": OTHER_TENANT}
CONTEXT = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_wa", roles=["owner"])
URL = "/api/v1/contact-suppressions"

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean_list() -> Iterator[None]:
    suppression_store.clear()
    yield
    suppression_store.clear()


def _audits(action: str) -> list:
    return [record for record in store.audit_records if record.action == action]


def _add(recipient: str, who: dict[str, str] = OWNER, **extra: Any) -> Any:
    return client.post(URL, headers=who, json={"recipient": recipient, "reason": "Asked by phone", **extra})


# Normalization and keywords ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+971 50 123 4567", ("phone", "+971501234567")),
        ("00971501234567", ("phone", "+971501234567")),
        ("050 123 4567", ("phone", "+971501234567")),
        ("971501234567", ("phone", "+971501234567")),
        (" <Ahmed@Example.COM> ", ("email", "ahmed@example.com")),
        ("not a contact", None),
        ("12", None),
    ],
)
def test_normalize(raw: str, expected: tuple[str, str] | None) -> None:
    assert normalize(raw) == expected


def test_mask_and_hash_never_keep_the_address() -> None:
    assert mask("phone", "+971501234567") == "+971•••4567"
    assert mask("email", "ahmed@example.com") == "a•••@example.com"
    digest = recipient_hash(TENANT, "+971501234567")
    assert len(digest) == 64 and "971501234567" not in digest
    assert digest != recipient_hash(OTHER_TENANT, "+971501234567")


@pytest.mark.parametrize(
    ("text", "keyword"),
    [("STOP", "stop"), (" stop. ", "stop"), ("Unsubscribe", "stop"), ("إيقاف", "stop"), ("ايقاف", "stop"),
     ("لا ترسلوا لي رسائل", "stop"), ("START", "start"), ("ابدأ", "start"), ("Please stop the order", None), ("", None)],
)
def test_keywords_match_whole_messages_only(text: str, keyword: str | None) -> None:
    assert keyword_of(text) == keyword


# The list ---------------------------------------------------------------------------------


def test_owner_adds_searches_and_removes_entries_with_a_reason() -> None:
    added = _add("+971 50 123 4567", channel="whatsapp", purpose="marketing")
    assert added.status_code == 201, added.text
    entry = added.json()
    assert entry["masked"] == "+971•••4567" and entry["source"] == "manual" and entry["created_by"] == "owner_wa"
    assert "recipient_hash" not in entry and "971501234567" not in str(entry)
    # Adding it again changes nothing; for every purpose it widens the entry.
    assert _add("00971501234567", channel="whatsapp", purpose="marketing").status_code == 200
    widened = _add("00971501234567", channel="whatsapp", purpose="all")
    assert widened.status_code == 200 and widened.json()["purpose"] == "all"
    _add("ahmed@example.com", channel="email")
    page = client.get(URL, headers=OWNER).json()
    assert page["total"] == 2 and page["can_export"] and page["can_manage"]
    assert [item["masked"] for item in client.get(URL, headers=OWNER, params={"q": "050 123 4567"}).json()["items"]] == ["+971•••4567"]
    assert client.get(URL, headers=OWNER, params={"q": "+971 55 000 0000"}).json()["total"] == 0
    assert client.get(URL, headers=OWNER, params={"channel": "email"}).json()["total"] == 1
    assert client.request("DELETE", f"{URL}/{entry['id']}", headers=OWNER, json={"reason": ""}).status_code == 422
    assert client.request("DELETE", f"{URL}/{entry['id']}", headers=OWNER, json={"reason": "Customer asked to start again"}).status_code == 204
    assert client.request("DELETE", f"{URL}/{entry['id']}", headers=OWNER, json={"reason": "again"}).status_code == 404
    assert client.get(URL, headers=OWNER).json()["total"] == 1
    removed = _audits("suppression.removed")[-1]
    assert removed.metadata["reason"] == "Customer asked to start again" and removed.metadata["masked"] == "+971•••4567"
    assert len(_audits("suppression.added")) == 3


def test_invalid_contacts_and_channel_mismatches_are_refused() -> None:
    assert _add("hello").status_code == 422
    assert _add("ahmed@example.com", channel="whatsapp").status_code == 422
    assert _add("+971501234567", channel="email").status_code == 422
    assert client.post(URL, headers=OWNER, json={"recipient": "+971501234567", "reason": "x"}).status_code == 422
    assert client.post(URL, headers=OWNER, json={"recipient": "+971501234567", "reason": "bad\x07reason"}).status_code == 422


def test_who_may_see_change_and_export_the_list() -> None:
    assert client.get(URL, headers=MEMBER).status_code == 403
    assert client.get(URL, headers=VIEWER).status_code == 403
    assert _add("+971501234567", who=MEMBER).status_code == 403
    # An organization administrator manages the list but only owners export it.
    assert _add("+971501234567", who=ORG_ADMIN_MEMBER).status_code == 201
    assert client.get(URL, headers=ORG_ADMIN_MEMBER).json()["can_export"] is False
    assert client.get(f"{URL}/export", headers=ORG_ADMIN_MEMBER).status_code == 403
    exported = client.get(f"{URL}/export", headers=OWNER)
    assert exported.status_code == 200 and exported.headers["content-type"].startswith("text/csv")
    assert "'+971•••4567" in exported.text and "971501234567" not in exported.text
    assert exported.headers["cache-control"] == "no-store" and _audits("suppression.exported")


def test_lists_are_per_tenant() -> None:
    _add("+971501234567", channel="all")
    assert client.get(URL, headers=OTHER_OWNER).json()["total"] == 0
    call = {"to": "+971501234567", "text": "Hello"}
    assert suppressed_for_call(CONTEXT, "send_whatsapp", call, None)
    other = TenantContext(tenant_id=OTHER_TENANT, workspace_id=WORKSPACE, user_id="owner", roles=["owner"])
    assert not suppressed_for_call(other, "send_whatsapp", call, None)
    entry = client.get(URL, headers=OWNER).json()["items"][0]
    assert client.request("DELETE", f"{URL}/{entry['id']}", headers=OTHER_OWNER, json={"reason": "not yours"}).status_code == 404


# The mediated tool layer ----------------------------------------------------------------------


def test_channel_and_purpose_decide_what_is_refused() -> None:
    _add("+971501234567", channel="whatsapp", purpose="marketing")
    _add("ahmed@example.com", channel="email", purpose="all")
    reply = ToolDefinition(name="send_whatsapp", description="", risk_level=RiskLevel.HIGH, category="whatsapp", action_kind="reply")
    campaign = ToolDefinition(name="marketing.send_campaign", description="", risk_level=RiskLevel.HIGH, category="marketing", action_kind="send")
    mail = ToolDefinition(name="send_email", description="", risk_level=RiskLevel.HIGH, category="email", action_kind="send")
    draft = ToolDefinition(name="draft_reply", description="", risk_level=RiskLevel.LOW, category="email", action_kind="draft")
    # A marketing-only WhatsApp opt-out lets a service reply through but not marketing.
    assert not suppressed_for_call(CONTEXT, reply.name, {"to": "971501234567"}, reply)
    assert suppressed_for_call(CONTEXT, reply.name, {"to": "971501234567", "purpose": "marketing"}, reply)
    assert suppressed_for_call(CONTEXT, campaign.name, {"recipients": ["+971501234567"]}, campaign)
    assert suppressed_for_call(CONTEXT, mail.name, {"to": "Ahmed@Example.com", "subject": "Order"}, mail)
    assert suppressed_for_call(CONTEXT, mail.name, {"to": "x@example.org", "cc": ["ahmed@example.com"]}, mail)
    assert not suppressed_for_call(CONTEXT, draft.name, {"to": "ahmed@example.com"}, draft)
    # An e-mail entry never stops a WhatsApp send to a number.
    assert not suppressed_for_call(CONTEXT, reply.name, {"to": "+971509999999"}, reply)


def _registry(sent: list[ToolCall]) -> ToolRegistry:
    registry = ToolRegistry()

    async def handler(call: ToolCall, context: TenantContext) -> ToolResult:
        sent.append(call)
        return ToolResult(status="succeeded", summary="sent")

    registry.register(ToolDefinition(name="send_whatsapp", description="Send", risk_level=RiskLevel.MEDIUM,
                                     category="whatsapp", action_kind="send"), handler)
    return registry


def test_the_runtime_and_the_registry_refuse_a_suppressed_recipient_with_the_reason() -> None:
    sent: list[ToolCall] = []
    registry = _registry(sent)
    runtime = AgentRuntime(MockModelGateway(), InMemoryRepository(InMemoryStore()), tools=registry)
    call = ToolCall(name="send_whatsapp", arguments={"to": "971501234567", "text": "New collection"})
    assert runtime.evaluate(call, CONTEXT).outcome != ToolPolicyOutcome.BLOCK
    apply_inbound_keyword(CONTEXT, SuppressionChannel.WHATSAPP, "971501234567", "stop", "test")
    decision = runtime.evaluate(call, CONTEXT)
    assert decision.outcome == ToolPolicyOutcome.BLOCK and "opted out" in decision.reason and "+971•••4567" in decision.reason
    assert "contact_suppression" in decision.governance_rules
    # Even a path that reaches the registry directly is refused, and nothing is sent.
    result = asyncio.run(registry.execute(call, CONTEXT))
    assert result.status == "blocked" and "opted out" in result.summary and sent == []
    stages = [record.metadata["stage"] for record in _audits("suppression.send_refused")]
    assert "policy" in stages and "execute" in stages
    assert all("971501234567" not in str(record.metadata) for record in _audits("suppression.send_refused"))


def test_an_unsubscribe_email_suppresses_marketing_email_from_that_sender() -> None:
    now = utc_now()
    base = {"tenant_id": TENANT, "workspace_id": WORKSPACE, "account_id": "acc", "thread_id": "t", "received_at": now,
            "created_at": now, "updated_at": now}
    email_inbox._record_unsubscribes(CONTEXT, [
        EmailMessage(id="m1", provider_message_id="p1", subject="Re: Unsubscribe", from_address="Ahmed@Example.com", **base),
        EmailMessage(id="m2", provider_message_id="p2", subject="Unsubscribe me from the order update?", from_address="b@example.com", **base),
    ])
    items = client.get(URL, headers=OWNER).json()["items"]
    assert [(i["masked"], i["channel"], i["purpose"], i["source"]) for i in items] == [("a•••@example.com", "email", "marketing", "email_unsubscribe")]


# WhatsApp end to end ----------------------------------------------------------------------


def _template_send(conversation_id: str) -> Any:
    template = {"name": "order_update", "language": "en_US", "parameters": ["Ahmed", "A-1001"]}
    return client.post(f"{BASE}/conversations/{conversation_id}/send", headers=MEMBER, json={"template": template})


def test_whatsapp_stop_suppresses_every_send_and_start_lifts_it(meta: FakeMeta) -> None:  # noqa: F811
    connect()
    client.put(f"{BASE}/account", headers=OWNER, json={"phone_number_id": "106540352242922", "business_account_id": "102290129340398",
                                                       "auto_send_templates": ["order_update"]})
    deliver(inbound("Where is my order?", age=timedelta(minutes=5)))
    conversation = only_conversation()
    assert _template_send(conversation["id"]).json()["outcome"] == "sent"
    pending = client.post(f"{BASE}/conversations/{conversation['id']}/send", headers=MEMBER, json={"text": "It ships today."}).json()
    assert pending["outcome"] == "awaiting_approval"

    assert deliver(inbound("STOP", wamid="wamid.stop")).status_code == 200
    items = client.get(URL, headers=OWNER).json()["items"]
    assert [(i["channel"], i["purpose"], i["source"], i["created_by"]) for i in items] == [("whatsapp", "all", "whatsapp_keyword", "system:inbound")]
    # A repeated delivery of the same message changes nothing.
    deliver(inbound("STOP", wamid="wamid.stop"))
    assert len(_audits("suppression.added")) == 1

    sent_before = len(meta.sent)
    refused = _template_send(conversation["id"])
    assert refused.status_code == 403 and "opted out" in refused.json()["error"]["message"]
    # The approval that was waiting shows the opt-out, and approving it sends nothing.
    approval = next(a for a in client.get("/api/v1/approvals", headers=OWNER).json() if a["id"] == pending["approval"]["id"])
    assert approval["suppressed"] == [{"recipient": "+966•••0001", "channel": "whatsapp", "purpose": "all"}]
    decided = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": approval["payload_hash"]})
    assert decided.status_code == 200 and decided.json()["run"]["status"] == "failed"
    assert "opted out" in decided.json()["run"]["steps"][-1]["summary"]
    assert len(meta.sent) == sent_before and _audits("suppression.send_refused")

    # START removes the keyword entry; a manual entry stays.
    _add("+966500000002", channel="whatsapp")
    deliver(inbound("ابدأ", wamid="wamid.start"))
    assert [i["source"] for i in client.get(URL, headers=OWNER).json()["items"]] == ["manual"]
    assert _template_send(conversation["id"]).json()["outcome"] == "sent"


def test_arabic_opt_out_phrase_is_honoured(meta: FakeMeta) -> None:  # noqa: F811
    connect()
    deliver(inbound("لا ترسلوا لي رسائل", wamid="wamid.ar"))
    assert client.get(URL, headers=OWNER).json()["total"] == 1


def test_email_check_is_linear_on_long_dotted_input() -> None:
    # CodeQL py/polynomial-redos: the old regex backtracked on "!@!." + "!." * n.
    import time

    from anum_api.suppression import normalize

    started = time.perf_counter()
    assert normalize("!@!." + "!." * 100_000) is None
    assert time.perf_counter() - started < 0.5
    assert normalize("Owner@Example.COM") == ("email", "owner@example.com")
    assert normalize("<a@b.co>") == ("email", "a@b.co")
    for bad in ("a@b", "@b.co", "a@b.", "a b@c.co", "a@b@c.co", "x" * 250 + "@b.co"):
        assert normalize(bad) is None
