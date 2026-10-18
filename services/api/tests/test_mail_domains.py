"""Mail domains and aliases on Mailcow (docs/email.md#mail-domains), in memory with a fake Mailcow.

Only platform operators read or ask; every change waits for an approval bound to its payload
hash and is checked again against Mailcow when approved. Domains outside
ANUM_MAILSERVER_ALLOWED_DOMAINS are shown, never changed. DNS answers come from a fake lookup.
Every key here is a plainly fake fixture.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.mailbox import domain_dns, domains_api, mailcow
from anum_api.mailbox.config import mail_settings
from anum_api.settings import settings
from anum_api.store import store

client = TestClient(main.app)
BASE = "/api/v1/platform/mail/domains"
OPERATOR = {"x-tenant-id": "tenant_ops_dom", "x-workspace-id": "workspace_ops_dom", "x-user-id": "operator_dom", "x-user-roles": "owner,platform_operator"}
OWNER_ONLY = {**OPERATOR, "x-user-roles": "owner"}


class FakeMailcow:
    def __init__(self) -> None:
        self.domains: dict[str, dict[str, Any]] = {"example.test": {"mboxes_in_domain": 2}, "legacy.test": {"mboxes_in_domain": 0}}
        self.aliases: list[dict[str, Any]] = [{"id": 7, "address": "sales@example.test", "goto": "owner@example.test", "active": "1"}]
        self.posts: list[tuple[str, Any, str]] = []
        self.refuse_writes = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        key = request.headers["x-api-key"]
        if request.method == "GET":
            assert key == "example-readonly-key"
            if path == "/api/v1/get/domain/all":
                return httpx.Response(200, json=[{"domain_name": d, "active": "1", **v} for d, v in self.domains.items()])
            if path == "/api/v1/get/alias/all":
                return httpx.Response(200, json=self.aliases)
            if path.startswith("/api/v1/get/dkim/"):
                return httpx.Response(200, json={"dkim_txt": "v=DKIM1;k=rsa;t=s;s=email;p=EXAMPLEKEY", "dkim_selector": "dkim", "pubkey": "EXAMPLEKEY", "privkey": "example-private"})
            return httpx.Response(404, json={})
        body = json.loads(request.content)
        self.posts.append((path, body, key))
        if self.refuse_writes:
            return httpx.Response(200, json=[{"type": "danger", "msg": ["domain_exists"]}])
        if path == "/api/v1/add/domain":
            self.domains[body["domain"]] = {"mboxes_in_domain": 0}
        elif path == "/api/v1/delete/domain":
            for name in body:
                self.domains.pop(name, None)
        elif path == "/api/v1/add/alias":
            self.aliases.append({"id": 100 + len(self.aliases), "address": body["address"], "goto": body["goto"], "active": "1"})
        elif path == "/api/v1/delete/alias":
            self.aliases = [a for a in self.aliases if str(a["id"]) not in body]
        return httpx.Response(200, json=[{"type": "success", "msg": ["ok"]}])


@pytest.fixture
def cow(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeMailcow]:
    fake = FakeMailcow()
    monkeypatch.setattr(settings, "repository_backend", "memory")
    for name in ("tasks", "runs", "approvals", "events", "audit_records"):
        getattr(store, name).clear()
    monkeypatch.setattr(mailcow, "mailcow_transport", httpx.MockTransport(fake.handle))
    cfg = mailcow.mailcow_settings
    monkeypatch.setattr(cfg, "api_enabled", True)
    monkeypatch.setattr(cfg, "api_url", "https://mail.example.test")
    monkeypatch.setattr(cfg, "api_key", mailcow.SecretStr("example-readonly-key"))
    monkeypatch.setattr(cfg, "write_enabled", True)
    monkeypatch.setattr(cfg, "api_write_key", mailcow.SecretStr("example-write-key"))
    monkeypatch.setattr(cfg, "mail_host", "mail.example.test")
    monkeypatch.setattr(mail_settings, "allowed_domains", "example.test,new.test")
    yield fake


def approve(approval: dict) -> httpx.Response:
    return client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OPERATOR, json={"payload_hash": approval["payload_hash"]})


def ask(**body: Any) -> httpx.Response:
    return client.post(f"{BASE}/changes", headers=OPERATOR, json=body)


def test_only_platform_operators_see_or_change_domains(cow: FakeMailcow) -> None:
    for call in (lambda: client.get(BASE, headers=OWNER_ONLY), lambda: client.get(f"{BASE}/example.test/dns", headers=OWNER_ONLY),
                 lambda: client.post(f"{BASE}/changes", headers=OWNER_ONLY, json={"operation": "add_domain", "domain": "new.test"}),
                 lambda: client.delete(f"{BASE}/changes/approval_x", headers=OWNER_ONLY)):
        response = call()
        assert response.status_code == 403, response.text
    assert cow.posts == []


def test_lists_mailcow_and_allowed_domains_with_aliases(cow: FakeMailcow) -> None:
    body = client.get(BASE, headers=OPERATOR).json()
    assert body["mailcow_state"] == "ok" and body["can_change"] is True and body["mail_host"] == "mail.example.test"
    assert body["allowed_domains"] == ["example.test", "new.test"]
    rows = {d["domain"]: d for d in body["domains"]}
    assert rows["example.test"]["in_mailcow"] and rows["example.test"]["allowed"] and rows["example.test"]["mailboxes"] == 2
    assert rows["example.test"]["aliases"] == [{"id": "7", "address": "sales@example.test", "goto": ["owner@example.test"], "active": True}]
    assert rows["new.test"]["in_mailcow"] is False and rows["new.test"]["allowed"] is True
    assert rows["legacy.test"]["allowed"] is False
    # Allowed first, then the rest.
    assert [d["domain"] for d in body["domains"]] == ["example.test", "new.test", "legacy.test"]
    assert "example-readonly-key" not in json.dumps(body) and "example-write-key" not in json.dumps(body)


def test_adding_a_domain_waits_for_an_approval_of_that_exact_change(cow: FakeMailcow) -> None:
    waiting = ask(operation="add_domain", domain="New.Test")
    assert waiting.status_code == 200, waiting.text
    approval = waiting.json()["approval"]
    assert approval["action"] == domains_api.DOMAIN_TOOL and approval["arguments"]["domain"] == "new.test" and approval["payload_hash"]
    assert cow.posts == []  # nothing changed yet
    listed = client.get(BASE, headers=OPERATOR).json()
    assert [p["domain"] for p in listed["pending"]] == ["new.test"]
    assert ask(operation="add_domain", domain="new.test").status_code == 409  # already waiting
    wrong = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OPERATOR, json={"payload_hash": "0" * 64})
    assert wrong.status_code in {409, 422} and cow.posts == []
    assert approve(approval).status_code == 200
    assert [(path, key) for path, _, key in cow.posts] == [("/api/v1/add/domain", "example-write-key")]
    assert cow.posts[0][1]["domain"] == "new.test"
    after = client.get(BASE, headers=OPERATOR).json()
    assert after["pending"] == [] and {d["domain"]: d for d in after["domains"]}["new.test"]["in_mailcow"] is True
    actions = {r.action for r in store.audit_records.values()} if isinstance(store.audit_records, dict) else {r.action for r in store.audit_records}
    assert "mail.domains.requested" in actions


def test_domains_outside_the_allowed_list_are_never_changed(cow: FakeMailcow) -> None:
    for body in ({"operation": "add_domain", "domain": "other.test"}, {"operation": "remove_domain", "domain": "legacy.test"},
                 {"operation": "add_alias", "address": "x@legacy.test", "goto": ["a@b.test"]}):
        response = ask(**body)
        assert response.status_code == 409 and "ANUM_MAILSERVER_ALLOWED_DOMAINS" in response.json()["error"]["message"]
    assert cow.posts == []


def test_bad_input_is_refused_before_any_approval(cow: FakeMailcow) -> None:
    assert ask(operation="add_domain", domain="not a domain").status_code == 422
    assert ask(operation="add_alias", address="no-at-sign", goto=["a@b.test"]).status_code == 422
    assert ask(operation="add_alias", address="team@example.test", goto=[]).status_code == 422
    assert ask(operation="add_alias", address="team@example.test", goto=["team@example.test"]).status_code == 422
    assert ask(operation="remove_alias", address="sales@example.test").status_code == 422  # no alias id
    assert ask(operation="remove_domain", domain="example.test").status_code == 409  # still has mailboxes
    assert ask(operation="add_alias", address="sales@example.test", goto=["a@b.test"]).status_code == 409  # exists
    assert store.approvals == {} or len(store.approvals) == 0


def test_alias_add_and_remove_after_approval(cow: FakeMailcow) -> None:
    added = ask(operation="add_alias", address="Team@Example.test", goto=["a@client.test", "A@client.test", "b@client.test"])
    assert added.status_code == 200, added.text
    assert added.json()["approval"]["arguments"]["goto"] == ["a@client.test", "b@client.test"]
    assert approve(added.json()["approval"]).status_code == 200
    assert cow.posts[-1] == ("/api/v1/add/alias", {"address": "team@example.test", "goto": "a@client.test,b@client.test", "active": "1"}, "example-write-key")
    removed = ask(operation="remove_alias", address="sales@example.test", alias_id="7")
    assert removed.status_code == 200
    assert approve(removed.json()["approval"]).status_code == 200
    assert cow.posts[-1][0] == "/api/v1/delete/alias" and cow.posts[-1][1] == ["7"]


def test_an_approval_of_something_that_changed_since_refuses(cow: FakeMailcow) -> None:
    waiting = ask(operation="add_domain", domain="new.test").json()["approval"]
    cow.domains["new.test"] = {"mboxes_in_domain": 0}  # someone added it in Mailcow meanwhile
    decided = approve(waiting)
    assert decided.status_code == 200
    assert cow.posts == []  # checked again, refused, nothing written
    # Writes switched off after asking: refused too.
    gone = ask(operation="add_alias", address="team@example.test", goto=["a@client.test"]).json()["approval"]
    mailcow.mailcow_settings.write_enabled = False
    approve(gone)
    assert cow.posts == []


def test_mailcow_refusing_the_change_is_reported_not_hidden(cow: FakeMailcow) -> None:
    waiting = ask(operation="add_domain", domain="new.test").json()["approval"]
    cow.refuse_writes = True
    body = approve(waiting).json()
    assert "domain_exists" in json.dumps(body)


def test_withdrawing_a_waiting_change(cow: FakeMailcow) -> None:
    waiting = ask(operation="add_domain", domain="new.test").json()["approval"]
    assert client.delete(f"{BASE}/changes/{waiting['id']}", headers=OPERATOR).status_code == 204
    assert client.get(BASE, headers=OPERATOR).json()["pending"] == []
    assert client.delete(f"{BASE}/changes/{waiting['id']}", headers=OPERATOR).status_code == 409
    assert client.delete(f"{BASE}/changes/approval_missing", headers=OPERATOR).status_code == 404


def test_changes_off_says_how_to_switch_them_on(cow: FakeMailcow, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mailcow.mailcow_settings, "write_enabled", False)
    body = client.get(BASE, headers=OPERATOR).json()
    assert body["can_change"] is False and "ANUM_MAILCOW_WRITE_ENABLED" in body["change_note"]
    refused = ask(operation="add_domain", domain="new.test")
    assert refused.status_code == 409 and "ANUM_MAILCOW_WRITE_ENABLED" in refused.json()["error"]["message"]


def test_mailcow_off_still_lists_the_allowed_domains(cow: FakeMailcow, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mailcow.mailcow_settings, "api_enabled", False)
    body = client.get(BASE, headers=OPERATOR).json()
    assert body["mailcow_state"] == "off" and body["can_change"] is False
    assert [d["domain"] for d in body["domains"]] == ["example.test", "new.test"]


# -- DNS ------------------------------------------------------------------------------------------


def _dns(monkeypatch: pytest.MonkeyPatch, answers: dict[tuple[str, str], list[str] | str]) -> list[tuple[str, str, float]]:
    asked: list[tuple[str, str, float]] = []

    def lookup(name: str, rdtype: str, timeout: float) -> list[str]:
        asked.append((name, rdtype, timeout))
        found = answers.get((name, rdtype), "nxdomain")
        if isinstance(found, str):
            raise domain_dns.LookupFailed(found)  # type: ignore[arg-type]
        return found

    monkeypatch.setattr(domain_dns, "lookup", lookup)
    return asked


def test_dns_check_reports_each_record_and_the_records_to_set(cow: FakeMailcow, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _dns(monkeypatch, {
        ("example.test", "MX"): ["10 mail.example.test"],
        ("example.test", "TXT"): ["v=spf1 +all", "google-site-verification=example"],
        ("dkim._domainkey.example.test", "TXT"): ["v=DKIM1;k=rsa;p=EXAMPLEKEY"],
        ("_dmarc.example.test", "TXT"): "timeout",
    })
    body = client.get(f"{BASE}/example.test/dns", headers=OPERATOR).json()
    assert body["mx"]["status"] == "ok"
    assert body["spf"]["status"] == "danger" and "+all" in body["spf"]["message"]
    assert body["dkim"]["status"] == "ok" and "matches" in body["dkim"]["message"]
    assert body["dmarc"]["status"] == "error" and "timed out" in body["dmarc"]["message"]
    records = {r["kind"]: r for r in body["records"]}
    assert records["mx"] == {"kind": "mx", "type": "MX", "name": "example.test", "value": "10 mail.example.test.", "note": None}
    assert records["dkim"]["name"] == "dkim._domainkey.example.test" and records["dkim"]["value"].endswith("p=EXAMPLEKEY")
    assert records["dmarc"]["name"] == "_dmarc.example.test" and records["spf"]["value"] == "v=spf1 mx ~all"
    assert all(timeout == mailcow.mailcow_settings.dns_timeout_seconds for _, _, timeout in asked) and len(asked) == 4
    assert "example-private" not in json.dumps(body)


def test_dns_check_only_for_known_domains(cow: FakeMailcow, monkeypatch: pytest.MonkeyPatch) -> None:
    _dns(monkeypatch, {})
    assert client.get(f"{BASE}/stranger.test/dns", headers=OPERATOR).status_code == 404
    assert client.get(f"{BASE}/legacy.test/dns", headers=OPERATOR).status_code == 200  # in Mailcow, not allowed: still checked
    assert client.get(f"{BASE}/bad_name/dns", headers=OPERATOR).status_code == 422
    missing = client.get(f"{BASE}/new.test/dns", headers=OPERATOR).json()
    assert {missing[k]["status"] for k in ("mx", "spf", "dkim", "dmarc")} == {"missing"}


@pytest.mark.parametrize(("record", "status"), [
    ("v=spf1 mx ~all", "ok"), ("v=spf1 mx -all", "ok"), ("v=spf1 include:spf.brevo.com ~all", "ok"),
    ("v=spf1 +all", "danger"), ("v=spf1 mx all", "danger"), ("v=spf1 mx ?all", "warning"), ("v=spf1 mx", "warning"),
    ("v=spf1 -all", "warning"),
])
def test_spf_judgement(record: str, status: str) -> None:
    assert domain_dns.check_spf([record], "mail.example.test").status == status


def test_two_spf_or_dmarc_records_are_dangerous_and_weak_dmarc_warns() -> None:
    assert domain_dns.check_spf(["v=spf1 mx ~all", "v=spf1 a ~all"], None).status == "danger"
    assert domain_dns.check_dmarc(["v=DMARC1; p=none"]).status == "warning"
    assert domain_dns.check_dmarc(["v=DMARC1; p=reject"]).status == "ok"
    assert domain_dns.check_dmarc(["v=DMARC1; p=reject", "v=DMARC1; p=none"]).status == "danger"
    assert domain_dns.check_mx(["10 other.example"], "mail.example.test").status == "warning"
    assert domain_dns.check_mx([], "mail.example.test").status == "missing"
    assert domain_dns.check_dkim(["v=DKIM1; p=OTHER"], "EXAMPLEKEY", "dkim").status == "warning"
    assert domain_dns.check_dkim(["v=DKIM1; p="], None, "dkim").status == "danger"
    assert domain_dns.valid_domain("Example.COM.") == "example.com" and domain_dns.valid_domain("-bad.com") is None
