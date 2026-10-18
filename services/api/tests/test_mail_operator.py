"""Email Center operator view (docs/email.md#operator-view) in memory, with the fake mail server.

The platform operator sees, per organisation they run, mailbox counts, health checks and
send outcomes, and nothing from the mail itself. Every address, password and key here is a
plainly fake fixture.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.mailbox import mailcow, monitor
from anum_api.mailbox.config import mail_settings
from anum_api.mailbox.server import SendUncertain
from anum_api.organisations import memory_branding
from anum_api.schemas import TenantContext
from anum_api.settings import settings
from anum_api.store import store
from test_mail_center import PASSWORD, FakeServer, fake  # noqa: F401 (fixture)

client = TestClient(main.app)
ORG = "acme_mail"
ORG_WS = "acme_mail_main"
OPERATOR = {"x-tenant-id": "tenant_ops_mail", "x-workspace-id": "workspace_ops_mail", "x-user-id": "operator_one", "x-user-roles": "owner,platform_operator"}
OWNER_ONLY = {**OPERATOR, "x-user-roles": "owner"}
OTHER_OPERATOR = {**OPERATOR, "x-user-id": "operator_two"}
CUSTOMER = {"x-tenant-id": ORG, "x-workspace-id": ORG_WS, "x-user-id": "acme_owner", "x-user-roles": "owner"}
ADDRESS = "owner@example.test"


@pytest.fixture
def org(fake: FakeServer, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeServer]:  # noqa: F811
    monkeypatch.setattr(settings, "repository_backend", "memory")
    memory_branding.clear()
    monitor.memory_counters.clear()
    for name in ("tasks", "runs", "approvals", "events", "tenants", "workspaces", "memberships", "invitations", "audit_records"):
        getattr(store, name).clear()
    created = client.post("/api/v1/platform/organizations", headers=OPERATOR, json={
        "tenant_id": ORG, "display_name": "Acme Mail", "workspace_name": "Main", "first_admin_email": "admin@acme.example"})
    assert created.status_code == 201, created.text
    yield fake
    memory_branding.clear()
    monitor.memory_counters.clear()


def _connect() -> dict:
    response = client.post("/api/v1/mail/accounts", headers=CUSTOMER, json={"address": ADDRESS, "password": PASSWORD})
    assert response.status_code == 201, response.text
    return response.json()


def _send(account: dict, key: str) -> None:
    draft = client.post("/api/v1/mail/drafts", headers=CUSTOMER, json={"account_id": account["id"]}).json()
    client.patch(f"/api/v1/mail/drafts/{draft['id']}", headers=CUSTOMER, json={"to": ["buyer@client.example"], "subject": "Private offer", "body": "Secret body text"})
    asked = client.post(f"/api/v1/mail/drafts/{draft['id']}/send", headers=CUSTOMER, json={"idempotency_key": key})
    assert asked.status_code in {200, 429}, asked.text
    if asked.status_code == 200:
        approval = asked.json()["approval"]
        client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=CUSTOMER, json={"payload_hash": approval["payload_hash"]})


def test_only_platform_operators_read_mail_health(org: FakeServer) -> None:
    for path in ("/api/v1/platform/mail/health", "/api/v1/platform/mail/server"):
        denied = client.get(path, headers=OWNER_ONLY)
        assert denied.status_code == 403 and "platform_operator" in denied.json()["error"]["message"]
        assert client.get(path, headers=CUSTOMER).status_code == 403
    assert client.get("/api/v1/platform/mail/health?days=0", headers=OPERATOR).status_code == 422
    assert client.get("/api/v1/platform/mail/health?days=91", headers=OPERATOR).status_code == 422


def test_counts_sends_failures_unknowns_and_limits_without_content(org: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    quiet = client.get("/api/v1/platform/mail/health", headers=OPERATOR).json()
    assert quiet["organisations"][0]["health"] == "quiet" and quiet["mailboxes"] == 0
    account = _connect()
    _send(account, "send-key-op-01")
    org.send_error = SendUncertain("The connection broke while the message was being sent. Check Sent before trying again.")
    _send(account, "send-key-op-02")
    org.send_error = None
    monkeypatch.setattr(mail_settings, "send_per_hour", 2)
    _send(account, "send-key-op-03")  # refused: two sends this hour already
    org.offline = True
    assert client.get(f"/api/v1/mail/accounts/{account['id']}/folders", headers=CUSTOMER).status_code == 503
    org.offline = False

    view = client.get("/api/v1/platform/mail/health", headers=OPERATOR)
    assert view.status_code == 200, view.text
    body = view.json()
    row = body["organisations"][0]
    assert row["tenant_id"] == ORG and row["display_name"] == "Acme Mail"
    assert row["mailboxes"] == 1 and row["offline"] == 1 and row["last_sync_at"] is not None
    assert row["counts"]["sent"] == 1 and row["counts"]["send_unknown"] == 1 and row["counts"]["send_rate_limited"] == 1
    assert row["counts"]["server_offline"] == 1 and row["health"] == "attention"
    assert body["totals"]["sent"] == 1 and body["mailboxes"] == 1
    # Counts only: no address, subject, body, password or error text anywhere in the answer.
    text = json.dumps(body)
    for secret in (ADDRESS, "buyer@client.example", "Private offer", "Secret body text", PASSWORD, "cannot be reached", "acme_owner"):
        assert secret not in text
    # Another operator does not see organisations they do not run.
    assert client.get("/api/v1/platform/mail/health", headers=OTHER_OPERATOR).json()["organisations"] == []


def test_connect_rate_limits_and_refused_passwords_are_counted(org: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mail_settings, "connect_attempts_per_hour", 1)
    assert client.post("/api/v1/mail/accounts", headers=CUSTOMER, json={"address": ADDRESS, "password": "example-wrong"}).status_code == 422
    assert client.post("/api/v1/mail/accounts", headers=CUSTOMER, json={"address": ADDRESS, "password": PASSWORD}).status_code == 429
    monkeypatch.setattr(mail_settings, "connect_attempts_per_hour", 5)
    account = _connect()
    org.password = "example-rotated"
    assert client.get(f"/api/v1/mail/accounts/{account['id']}/folders", headers=CUSTOMER).status_code == 409
    row = client.get("/api/v1/platform/mail/health", headers=OPERATOR).json()["organisations"][0]
    assert row["counts"]["connect_rate_limited"] == 1 and row["counts"]["auth_failed"] == 1 and row["auth_failed"] == 1


def test_a_counter_that_cannot_be_written_never_breaks_mail(org: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: object) -> None:
        raise RuntimeError("database gone")

    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(monitor, "_bump_sql", broken)
    monitor.bump(TenantContext(tenant_id=ORG, workspace_id=ORG_WS, user_id="acme_owner", roles=["owner"]), "sent")
    with pytest.raises(ValueError):
        monitor.bump(TenantContext(tenant_id=ORG, workspace_id=ORG_WS, user_id="acme_owner", roles=["owner"]), "made_up")  # type: ignore[arg-type]


# -- Mailcow, read only -----------------------------------------------------------------------


def _mailcow(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(mailcow, "mailcow_transport", httpx.MockTransport(record))
    monkeypatch.setattr(mailcow.mailcow_settings, "api_enabled", True)
    monkeypatch.setattr(mailcow.mailcow_settings, "api_url", "https://mail.example.test")
    monkeypatch.setattr(mailcow.mailcow_settings, "api_key", mailcow.SecretStr("example-readonly-key"))
    return seen


def test_mailcow_is_off_by_default(org: FakeServer) -> None:
    body = client.get("/api/v1/platform/mail/server", headers=OPERATOR).json()
    assert body["mailcow"]["state"] == "off" and body["server"]["enabled"] is True
    assert "password" not in json.dumps(body) and "key" not in json.dumps(body["server"])


def test_mailcow_reads_only_fixed_paths_and_returns_counts(org: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {
        "/api/v1/get/status/version": {"version": "2026-09"},
        "/api/v1/get/status/containers": {"postfix-mailcow": {"state": "running"}, "dovecot-mailcow": {"state": "exited"}},
        "/api/v1/get/mailq/all": [{"sender": "someone@client.example", "recipients": ["x@client.example"]}],
        "/api/v1/get/domain/all": [{"domain_name": "example.test", "active": "1", "mboxes_in_domain": 3,
                                     "max_num_mboxes_for_domain": 10, "max_quota_for_domain": 1000, "quota_used_in_domain": 250}],
    }
    seen = _mailcow(monkeypatch, lambda request: httpx.Response(200, json=answers[request.url.path]))
    body = client.get("/api/v1/platform/mail/server", headers=OPERATOR).json()["mailcow"]
    assert body["state"] == "ok" and body["version"] == "2026-09"
    assert body["containers_total"] == 2 and body["containers_not_running"] == ["dovecot-mailcow"]
    assert body["queue_messages"] == 1 and "someone@client.example" not in json.dumps(body)
    assert body["domains"] == [{"domain": "example.test", "active": True, "mailboxes": 3, "max_mailboxes": 10, "quota_used_percent": 25.0}]
    assert {r.method for r in seen} == {"GET"} and {r.url.path for r in seen} == set(mailcow.READ_PATHS.values())
    assert all(r.headers["x-api-key"] == "example-readonly-key" for r in seen)
    assert "example-readonly-key" not in json.dumps(body)


def test_mailcow_refusals_and_bad_settings_are_explained(org: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    _mailcow(monkeypatch, lambda request: httpx.Response(401, json={"type": "error"}))
    assert client.get("/api/v1/platform/mail/server", headers=OPERATOR).json()["mailcow"]["state"] == "refused"

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    _mailcow(monkeypatch, offline)
    assert client.get("/api/v1/platform/mail/server", headers=OPERATOR).json()["mailcow"]["state"] == "unreachable"
    for url in ("http://mail.example.test", "https://mail.example.test/api", "https://user:x@mail.example.test", ""):
        monkeypatch.setattr(mailcow.mailcow_settings, "api_url", url)
        monkeypatch.setattr(settings, "environment", "production")
        state = mailcow.read_status()
        assert state.state == "misconfigured" and "ANUM_MAILCOW_API_KEY" in (state.message or "")
    monkeypatch.setattr(mailcow.mailcow_settings, "api_url", "https://mail.example.test")
    monkeypatch.setattr(mailcow.mailcow_settings, "api_key", mailcow.SecretStr(""))
    assert mailcow.read_status().state == "misconfigured"


def test_the_mail_service_never_uses_the_mailcow_client() -> None:
    from pathlib import Path

    package = Path(mailcow.__file__).parent
    for name in ("service.py", "server.py", "api.py", "store.py"):
        assert "mailcow" not in (package / name).read_text(encoding="utf-8").lower()
