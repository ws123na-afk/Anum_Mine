"""The Email switch's proof: channels, the test email, and the event listener (docs/notifications.md#email)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from anum_api import notification_email
from anum_api.dependencies import memory_repository
from anum_api.events import CanonicalEventName, create_event
from anum_api.notifications import EmailRefused
from anum_api.schemas import TenantContext
from anum_api.settings import settings

from test_invitation_email import N_TENANT, N_WORKSPACE, Outbox, client, failed_event, outbox, team  # noqa: F401

OWNER = {"x-tenant-id": N_TENANT, "x-workspace-id": N_WORKSPACE, "x-user-id": "owner_a", "x-user-roles": "owner"}


def test_channels_say_what_email_can_do_for_the_caller(client: TestClient, outbox: Outbox, team: None) -> None:
    got = client.get("/api/v1/notification-channels", headers=OWNER)
    assert got.status_code == 200, got.text
    assert got.json()["email"] == {"configured": True, "address_known": True}


def test_channels_without_a_mail_server_or_an_address(client: TestClient, monkeypatch: pytest.MonkeyPatch, team: None) -> None:
    monkeypatch.setattr(settings, "smtp_host", None)
    assert client.get("/api/v1/notification-channels", headers=OWNER).json()["email"] == {"configured": False, "address_known": False}
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "anum@example.com")
    stranger = {**OWNER, "x-user-id": "nobody"}
    assert client.get("/api/v1/notification-channels", headers=stranger).json()["email"] == {"configured": True, "address_known": False}


def test_test_email_goes_only_to_the_callers_address_and_is_audited(client: TestClient, outbox: Outbox, team: None) -> None:
    sent = client.post("/api/v1/notification-channels/email-test", headers=OWNER)
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == "sent" and sent.json()["to"] == "owner@example.com"
    assert outbox.sent[0][0] == "owner@example.com" and "test email" in outbox.sent[0][1]
    outbox.failures = [EmailRefused("The mail server refused the message (550)")]
    failed = client.post("/api/v1/notification-channels/email-test", headers=OWNER).json()
    assert failed["status"] == "failed" and "550" in failed["error"]
    nobody = client.post("/api/v1/notification-channels/email-test", headers={**OWNER, "x-user-id": "nobody"}).json()
    assert nobody["status"] == "no_address"
    scope = TenantContext(tenant_id=N_TENANT, workspace_id=N_WORKSPACE, user_id="owner_a", roles=["owner"])
    audits = [r for r in memory_repository.list_audit_records(scope) if r.action == "notification.email_test"]
    assert sorted(a.outcome for a in audits) == ["failure", "failure", "success"]


def test_test_email_without_a_mail_server_says_so(client: TestClient, monkeypatch: pytest.MonkeyPatch, team: None) -> None:
    monkeypatch.setattr(settings, "smtp_host", None)
    result = client.post("/api/v1/notification-channels/email-test", headers=OWNER).json()
    assert result["status"] == "not_configured" and "ANUM_SMTP_HOST" in result["error"]


def test_send_email_marks_an_unreachable_server_as_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    import smtplib

    from anum_api import notifications

    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "anum@example.com")
    monkeypatch.setattr(settings, "smtp_security", "starttls")

    def refuse(*_: object, **__: object) -> None:
        raise ConnectionRefusedError("no route")

    monkeypatch.setattr(smtplib, "SMTP", refuse)
    with pytest.raises(EmailRefused) as raised:
        notifications.send_email("a@example.com", "s", "t")
    assert raised.value.transient and "could not be reached" in str(raised.value)
    assert "no route" not in str(raised.value)


def test_retries_back_off_between_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api import notifications

    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "anum@example.com")
    delays: list[float] = []
    calls: list[int] = []

    def flaky(*_: object) -> None:
        calls.append(1)
        raise EmailRefused("down", transient=True)

    monkeypatch.setattr(notifications, "send_email", flaky)
    outcome = notifications.send_with_retries("a@example.com", "s", "t", backoff_seconds=2, sleep=delays.append)
    assert outcome.status == "failed" and outcome.attempts == 3 and len(calls) == 3
    assert delays == [2, 4]


def test_listener_queues_only_emailed_kinds_and_only_with_a_mail_server(team: None, outbox: Outbox, monkeypatch: pytest.MonkeyPatch) -> None:
    queued: list = []
    monkeypatch.setattr(notification_email, "_executor", lambda: type("P", (), {"submit": lambda self, fn, i: queued.append(i)})())
    notification_email.submit([failed_event()])
    assert [i.kind.value for i in queued] == ["automation_failed"]
    ctx = TenantContext(tenant_id=N_TENANT, workspace_id=N_WORKSPACE, user_id="owner_a", roles=["owner"])
    notice = create_event(CanonicalEventName.AUTOMATION_NOTIFICATION, ctx, "run_1", {"push": True, "notify_user_ids": ["owner_a"]}).event
    notification_email.submit([notice])  # an in-app automation notice is not a failure email
    assert len(queued) == 1
