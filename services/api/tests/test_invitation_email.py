"""Invitation email and notification email through ANUM's system mail (docs/identity.md, docs/notifications.md).

SMTP itself is replaced at ``notifications.send_email``; the retry, the audit and the states
(sent, failed, retried, not configured, not requested) are the real code.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from anum_api import notification_email, notifications
from anum_api.dependencies import memory_repository
from anum_api.events import CanonicalEventName, create_event
from anum_api.notifications import EmailRefused
from anum_api.push import intent_for
from anum_api.onboarding import NotificationPreferences, _notifications
from anum_api.schemas import TenantContext, WorkspaceMembership, utc_now
from anum_api.settings import settings
from anum_api.store import store
from anum_api.team_models import MemberProfile
from anum_api.team_store import memory_team_store
from anum_api.workspace_members import invitation_link_base

from test_workspace_members import OWNER, TENANT, WORKSPACE, accept, client, headers_for, scope  # noqa: F401

APP = "https://app.example.com"


class Outbox:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.failures: list[Exception] = []

    def __call__(self, to: str, subject: str, text: str) -> None:
        if self.failures:
            raise self.failures.pop(0)
        self.sent.append((to, subject, text))


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> Outbox:
    box = Outbox()
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "anum@example.com")
    monkeypatch.setattr(settings, "smtp_retry_backoff_seconds", 0)
    monkeypatch.setattr(settings, "cors_origins", [APP])
    monkeypatch.setattr(settings, "public_app_url", "")
    monkeypatch.setattr(notifications, "send_email", box)
    return box


def create(client: TestClient, **body):
    response = client.post("/api/v1/workspace-invitations", headers=OWNER, json={"role": "member", **body})
    assert response.status_code == 201, response.text
    return response.json()


def email_audit() -> list:
    return [r for r in memory_repository.list_audit_records(scope()) if r.action == "workspace_invitation.email"]


def test_invitation_is_emailed_with_the_accept_link_and_audited(client: TestClient, outbox: Outbox) -> None:
    created = create(client, invitee_email="Sara@Example.com", app_url=f"{APP}/")
    assert created["email"] == {"status": "sent", "to": "Sara@Example.com", "attempts": 1, "error": None, "link_included": True}
    ((to, subject, text),) = outbox.sent
    assert to == "Sara@Example.com" and "invited" in subject
    assert f"{APP}/#invitation={created['token']}&workspace={WORKSPACE}" in text
    (record,) = email_audit()
    assert record.outcome == "success" and record.metadata["attempts"] == 1
    assert created["token"] not in str(record)
    # The emailed token works.
    assert accept(client, created["token"], headers_for("sara", email="sara@example.com", roles="viewer")).status_code == 200


def test_transient_failure_is_retried_then_sent(client: TestClient, outbox: Outbox) -> None:
    outbox.failures = [EmailRefused("The mail server could not be reached (TimeoutError)", transient=True)]
    created = create(client, invitee_email="sara@example.com", app_url=APP)
    assert created["email"]["status"] == "sent" and created["email"]["attempts"] == 2
    assert len(outbox.sent) == 1


def test_permanent_refusal_is_not_retried_and_the_owner_is_told(client: TestClient, outbox: Outbox) -> None:
    outbox.failures = [EmailRefused("The mail server refused the recipient address")]
    created = create(client, invitee_email="sara@example.com", app_url=APP)
    assert created["email"]["status"] == "failed" and created["email"]["attempts"] == 1
    assert "refused the recipient" in created["email"]["error"]
    assert created["token"].startswith("anum_inv_")  # still copyable
    (record,) = email_audit()
    assert record.outcome == "failure" and record.metadata["status"] == "failed"


def test_retries_stop_after_three_attempts(client: TestClient, outbox: Outbox) -> None:
    outbox.failures = [EmailRefused("down", transient=True) for _ in range(5)]
    created = create(client, invitee_email="sara@example.com", app_url=APP)
    assert created["email"]["status"] == "failed" and created["email"]["attempts"] == 3
    assert not outbox.sent


def test_without_a_mail_server_the_response_says_so(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "smtp_host", None)
    created = create(client, invitee_email="sara@example.com")
    assert created["email"]["status"] == "not_configured"
    assert "Copy the link" in created["email"]["error"]
    (record,) = email_audit()
    assert record.metadata["status"] == "not_configured"


def test_no_email_when_not_asked_or_no_address(client: TestClient, outbox: Outbox) -> None:
    assert create(client, invitee_email="sara@example.com", send_email=False)["email"]["status"] == "not_requested"
    assert create(client, invitee_user_id="user_x")["email"]["status"] == "not_requested"
    assert outbox.sent == [] and email_audit() == []


def test_link_origin_must_be_approved_else_the_email_carries_the_token(client: TestClient, outbox: Outbox) -> None:
    created = create(client, invitee_email="sara@example.com", app_url="https://evil.example.net/")
    assert created["email"]["link_included"] is False
    text = outbox.sent[0][2]
    assert created["token"] in text and "evil.example.net" not in text


def test_public_app_url_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "public_app_url", "https://anum.example.org/app#x")
    monkeypatch.setattr(settings, "cors_origins", [])
    assert invitation_link_base("https://other.example.com") == "https://anum.example.org/app"
    monkeypatch.setattr(settings, "public_app_url", "")
    assert invitation_link_base("https://other.example.com") is None
    assert invitation_link_base("javascript:alert(1)") is None


def test_resend_emails_the_new_token_and_the_old_one_dies(client: TestClient, outbox: Outbox) -> None:
    first = create(client, invitee_email="sara@example.com", app_url=APP)
    again = client.post(
        f"/api/v1/workspace-invitations/{first['invitation']['id']}/resend", headers=OWNER, json={"app_url": APP}
    )
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["email"]["status"] == "sent" and body["token"] != first["token"]
    assert body["token"] in outbox.sent[-1][2] and first["token"] not in outbox.sent[-1][2]
    assert accept(client, first["token"], headers_for("sara", email="sara@example.com")).status_code == 404


def test_email_status_route_is_owner_only(client: TestClient, outbox: Outbox, monkeypatch: pytest.MonkeyPatch) -> None:
    ok = client.get("/api/v1/workspace-invitations/email-status", headers=OWNER, params={"app_url": APP})
    assert ok.json() == {"configured": True, "link_base": f"{APP}/"}
    monkeypatch.setattr(settings, "smtp_host", None)
    assert client.get("/api/v1/workspace-invitations/email-status", headers=OWNER).json()["configured"] is False
    assert client.get("/api/v1/workspace-invitations/email-status", headers=headers_for("v1")).status_code == 403


def test_smtp_failures_become_safe_sentences() -> None:
    import smtplib

    assert notifications._smtp_failure(smtplib.SMTPAuthenticationError(535, b"bad creds secret")).args[0].startswith("The mail server refused ANUM's sign-in")
    assert "secret" not in notifications._smtp_failure(smtplib.SMTPAuthenticationError(535, b"bad creds secret")).args[0]
    assert notifications._smtp_failure(smtplib.SMTPServerDisconnected("x")).transient
    busy = notifications._smtp_failure(smtplib.SMTPResponseException(451, b"later"))
    assert busy.transient and "451" in busy.args[0]
    assert not notifications._smtp_failure(smtplib.SMTPResponseException(550, b"no")).transient


# --------------------------------------------------------------------------- notification email

N_TENANT, N_WORKSPACE = "tenant_ne", "workspace_ne"


@pytest.fixture
def team() -> Iterator[None]:
    def reset() -> None:
        _notifications.clear()
        store.memberships.clear()
        store.audit_records.clear()

    reset()
    now = utc_now()
    memory_repository.save_membership(
        WorkspaceMembership(tenant_id=N_TENANT, workspace_id=N_WORKSPACE, user_id="owner_a", role="owner", active=True, created_at=now, updated_at=now)
    )
    ctx = TenantContext(tenant_id=N_TENANT, workspace_id=N_WORKSPACE, user_id="owner_a", roles=["owner"])
    memory_team_store.save_profile(ctx, MemberProfile(user_id="owner_a", display_name="Owner", email="owner@example.com"))
    yield
    reset()


def failed_event():
    ctx = TenantContext(tenant_id=N_TENANT, workspace_id=N_WORKSPACE, user_id="owner_a", roles=["owner"])
    return create_event(
        CanonicalEventName.AUTOMATION_RUN_FAILED,
        ctx,
        "run_1",
        {"workflow_id": "wf_1", "run_id": "run_1", "workflow_name": "Nightly", "notify_user_ids": ["owner_a"], "error": "boom"},
    ).event


def run_email(event) -> dict[str, int]:
    intent = intent_for(event)
    assert intent is not None
    return notification_email.deliver_intent_email(intent)


def set_prefs(**values) -> None:
    ctx = TenantContext(tenant_id=N_TENANT, workspace_id=N_WORKSPACE, user_id="owner_a", roles=["owner"])
    _notifications.save(ctx, NotificationPreferences(**values))


def test_failed_automation_is_emailed_when_email_and_the_automation_switch_are_on(team: None, outbox: Outbox) -> None:
    set_prefs(email_enabled=True)
    assert run_email(failed_event())["sent"] == 1
    ((to, subject, text),) = outbox.sent
    assert to == "owner@example.com" and subject == "ANUM: Automation failed"
    assert "Nightly" not in text and "boom" not in text  # fixed text, no content
    audited = [r for r in memory_repository.list_audit_records(TenantContext(tenant_id=N_TENANT, workspace_id=N_WORKSPACE, user_id="owner_a", roles=["owner"])) if r.action == "notification.email"]
    assert audited and audited[0].metadata["status"] == "sent"


def test_switches_gate_the_email(team: None, outbox: Outbox) -> None:
    set_prefs(email_enabled=False)
    assert run_email(failed_event())["skipped"] == 1
    set_prefs(email_enabled=True, automation_failed=False)
    assert run_email(failed_event())["skipped"] == 1
    assert outbox.sent == []


def test_no_address_and_failures_are_recorded_not_raised(team: None, outbox: Outbox) -> None:
    set_prefs(email_enabled=True)
    outbox.failures = [EmailRefused("nope")]
    assert run_email(failed_event())["failed"] == 1
    memory_team_store.save_profile(
        TenantContext(tenant_id=N_TENANT, workspace_id=N_WORKSPACE, user_id="owner_a", roles=["owner"]),
        MemberProfile(user_id="owner_a", display_name="Owner", email=None),
    )
    assert run_email(failed_event())["no_address"] == 1


def test_nothing_is_queued_without_a_mail_server(team: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "smtp_host", None)
    queued: list = []
    monkeypatch.setattr(notification_email, "_executor", lambda: type("P", (), {"submit": lambda self, fn, i: queued.append(i)})())
    notification_email.submit([failed_event()])
    assert queued == []
