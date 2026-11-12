"""The email and WhatsApp delivery tools (docs/tools-and-integrations.md#delivery).

The end-to-end paths through a tyre report and an approval are in ``test_tyre_api.py``
(email) and ``test_whatsapp.py`` (WhatsApp document); this file checks each tool alone.
"""

from __future__ import annotations

import asyncio
import smtplib
from collections.abc import Iterator
from email.message import EmailMessage
from typing import Any

import pytest

from anum_api import delivery, delivery_tools, main, notifications
from anum_api.agent_tools import ToolCall
from anum_api.schemas import RiskLevel, TenantContext
from anum_api.settings import settings

CONTEXT = TenantContext(tenant_id="tenant_dt", workspace_id="workspace_dt", user_id="owner_dt", roles=["owner"])
FILE = {"bytes": b"%PDF-1.4 example report"}


@pytest.fixture(autouse=True)
def resolver() -> Iterator[None]:
    delivery.register_attachment_resolver("test_doc", lambda context, item_id, fmt: ("report.pdf", "application/pdf", FILE["bytes"]))
    FILE["bytes"] = b"%PDF-1.4 example report"
    yield
    delivery._resolvers.pop("test_doc", None)


@pytest.fixture
def mail_server(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "reports@example.com")

    def send(to: str, subject: str, text: str, attachments: Any = ()) -> None:
        sent.append({"to": to, "subject": subject, "text": text, "attachments": list(attachments)})

    monkeypatch.setattr(notifications, "send_email", send)
    return sent


def call(**arguments: Any) -> ToolCall:
    reference = delivery.attachment_reference("test_doc", "doc_1", "pdf", CONTEXT)
    return ToolCall(name=delivery_tools.EMAIL_TOOL, arguments={
        "to": "fleet@example.com", "subject": "Tyre report", "text": "The report is attached.", "attachments": [reference], **arguments})


def test_tools_are_registered_high_risk_and_always_ask() -> None:
    for name, channel in ((delivery_tools.EMAIL_TOOL, "email"), (delivery_tools.WHATSAPP_TOOL, "whatsapp")):
        definition = main.tool_registry.definition(name)
        assert definition is not None and definition.delivery_channel == channel
        assert definition.risk_level == RiskLevel.HIGH and definition.always_ask is True
        assert definition.required_roles == frozenset({"owner", "member"})


def test_email_is_a_channel_only_with_a_mail_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "smtp_host", None)
    reason = delivery_tools.email_unavailable(CONTEXT)
    assert reason and "No email integration is connected" in reason and "ANUM_SMTP_HOST" in reason
    assert "email" not in delivery.channels(main.tool_registry, CONTEXT)
    assert delivery.missing_reason("email", CONTEXT, main.tool_registry) == reason
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "reports@example.com")
    assert delivery_tools.email_unavailable(CONTEXT) is None
    assert delivery.channels(main.tool_registry, CONTEXT)["email"] == delivery_tools.EMAIL_TOOL


def test_whatsapp_is_a_channel_only_with_the_workspace_number() -> None:
    reason = delivery_tools.whatsapp_unavailable(CONTEXT)
    assert reason and "No whatsapp integration is connected for this workspace" in reason
    assert "whatsapp" not in delivery.channels(main.tool_registry, CONTEXT)


def test_email_tool_sends_exactly_the_approved_file(mail_server: list[dict[str, Any]]) -> None:
    result = asyncio.run(delivery_tools.email_tool(call(subject="Tyre report\r\nBcc: someone@example.com"), CONTEXT))
    assert result.status == "succeeded", result.summary
    assert mail_server == [{"to": "fleet@example.com", "subject": "Tyre report Bcc: someone@example.com",
                            "text": "The report is attached.", "attachments": [("report.pdf", "application/pdf", FILE["bytes"])]}]
    assert result.output["attachments"] == ["report.pdf"] and result.audit_metadata["outcome"] == "sent"


def test_email_tool_refuses_a_changed_file_or_a_bad_recipient(mail_server: list[dict[str, Any]]) -> None:
    approved = call()
    FILE["bytes"] = b"%PDF-1.4 a different report"
    changed = asyncio.run(delivery_tools.email_tool(approved, CONTEXT))
    assert changed.status == "failed" and "changed after it was approved" in changed.summary
    for to in ("two@example.com, three@example.com", "+971501234567", ""):
        bad = asyncio.run(delivery_tools.email_tool(call(to=to), CONTEXT))
        assert bad.status == "failed" and "one email address" in bad.summary
    many = asyncio.run(delivery_tools.email_tool(call(attachments=[{}] * 4), CONTEXT))
    assert many.status == "failed" and "at most 3" in many.summary
    assert mail_server == []


def test_email_tool_reports_the_mail_server_refusal(monkeypatch: pytest.MonkeyPatch, mail_server: list[dict[str, Any]]) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise notifications.EmailRefused("The mail server refused the recipient address")

    monkeypatch.setattr(notifications, "send_email", refuse)
    result = asyncio.run(delivery_tools.email_tool(call(), CONTEXT))
    assert result.status == "failed" and "refused the recipient address" in result.summary


def test_send_email_builds_a_message_with_the_attachment(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[EmailMessage] = []

    class FakeSmtp:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            assert host == "smtp.example.com"

        def send_message(self, message: EmailMessage) -> None:
            captured.append(message)

        def quit(self) -> None:
            return None

    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "reports@example.com")
    monkeypatch.setattr(settings, "smtp_security", "none")
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    notifications.send_email("fleet@example.com", "Tyre report", "Attached.", [("report.pdf", "application/pdf", b"%PDF-1.4 x")])
    message = captured[0]
    parts = list(message.iter_attachments())
    assert message["To"] == "fleet@example.com" and len(parts) == 1
    assert parts[0].get_filename() == "report.pdf" and parts[0].get_content_type() == "application/pdf"
    assert parts[0].get_content() == b"%PDF-1.4 x"
