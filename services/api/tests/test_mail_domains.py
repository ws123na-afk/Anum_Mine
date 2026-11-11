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
        self.mailboxes: list[dict[str, Any]] = [
            {"username": "owner@example.test", "name": "Owner", "active": "1", "quota": 3221225472, "percent_in_use": "12"},
            {"username": "info@example.test", "name": "Front  desk", "active": "0", "quota": 0, "percent_in_use": "-"},
        ]
        self.mailbox_read_status = 200
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
            if path == "/api/v1/get/mailbox/all":
                return httpx.Response(self.mailbox_read_status, json=self.mailboxes)
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
        elif path == "/api/v1/add/mailbox":
            address = f"{body['local_part']}@{body['domain']}"
            self.mailboxes.append({"username": address, "name": body["name"], "active": "1", "quota": int(body["quota"]) * 1048576})
            self.domains[body["domain"]]["mboxes_in_domain"] = self.domains[body["domain"]].get("mboxes_in_domain", 0) + 1
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
    assert records["mx"] == {"kind": "mx", "type": "MX", "name": "example.test", "value": "10 mail.example.test.", "note": None, "note_code": None}
    # Clients translate the code and its parameters; the raw DNS values stay as found.
    assert (body["mx"]["code"], body["mx"]["params"]) == ("mx_ok", {"host": "mail.example.test"})
    assert body["spf"]["code"] == "spf_dangerous_all" and body["spf"]["found"] == ["v=spf1 +all"]
    assert body["dkim"]["code"] == "dkim_ok"
    assert (body["dmarc"]["code"], body["dmarc"]["params"]) == ("lookup_timeout", {"record": "DMARC"})
    assert records["spf"]["note_code"] == "record_spf_one" and records["dmarc"]["note_code"] == "record_dmarc_start"
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


def test_every_judgement_has_a_code_clients_translate() -> None:
    cases = {
        "mx_missing": domain_dns.check_mx([], "mail.example.test"),
        "mx_null": domain_dns.check_mx(["0 ."], "mail.example.test"),
        "mx_unverified": domain_dns.check_mx(["10 a.example"], None),
        "mx_also_elsewhere": domain_dns.check_mx(["10 mail.example.test", "20 b.example"], "mail.example.test"),
        "mx_elsewhere": domain_dns.check_mx(["10 b.example"], "mail.example.test"),
        "spf_missing": domain_dns.check_spf([], None),
        "spf_multiple": domain_dns.check_spf(["v=spf1 mx ~all", "v=spf1 a ~all"], None),
        "spf_dangerous_all": domain_dns.check_spf(["v=spf1 +all"], None),
        "spf_neutral_all": domain_dns.check_spf(["v=spf1 mx ?all"], None),
        "spf_no_all": domain_dns.check_spf(["v=spf1 mx"], None),
        "spf_no_server": domain_dns.check_spf(["v=spf1 -all"], None),
        "spf_ok": domain_dns.check_spf(["v=spf1 mx ~all"], None),
        "dkim_missing": domain_dns.check_dkim([], None, "dkim"),
        "dkim_empty": domain_dns.check_dkim(["v=DKIM1; p="], None, "dkim"),
        "dkim_mismatch": domain_dns.check_dkim(["v=DKIM1; p=A"], "B", "dkim"),
        "dkim_unverified": domain_dns.check_dkim(["v=DKIM1; p=A"], None, "dkim"),
        "dkim_ok": domain_dns.check_dkim(["v=DKIM1; p=A"], "A", "dkim"),
        "dmarc_missing": domain_dns.check_dmarc([]),
        "dmarc_multiple": domain_dns.check_dmarc(["v=DMARC1; p=none", "v=DMARC1; p=reject"]),
        "dmarc_ok": domain_dns.check_dmarc(["v=DMARC1; p=reject"]),
        "dmarc_none": domain_dns.check_dmarc(["v=DMARC1; p=none"]),
        "dmarc_no_policy": domain_dns.check_dmarc(["v=DMARC1"]),
    }
    for code, check in cases.items():
        assert check.code == code, (code, check)
    assert cases["mx_elsewhere"].params == {"hosts": "b.example", "host": "mail.example.test"}
    assert cases["dkim_missing"].params == {"name": "dkim._domainkey"}
    assert cases["dmarc_ok"].params == {"policy": "reject"}
    # Without a DKIM key in Mailcow the record has no value yet, only a note to create one.
    dkim = next(r for r in domain_dns.expected_records("example.test", None, "dkim", None) if r.kind == "dkim")
    assert dkim.value == "" and dkim.note_code == "record_dkim_create"


# -- only platform operators, whatever their workspace role ----------------------------------------

MEMBER_OPERATOR = {**OPERATOR, "x-user-id": "operator_member", "x-user-roles": "member,platform_operator"}


def _context(headers: dict[str, str]) -> Any:
    from anum_api.schemas import TenantContext

    roles = headers["x-user-roles"].split(",")
    return TenantContext(
        tenant_id=headers["x-tenant-id"], workspace_id=headers["x-workspace-id"], user_id=headers["x-user-id"],
        roles=[r for r in roles if r != "platform_operator"], organization_roles=[r for r in roles if r == "platform_operator"],
    )


def test_a_workspace_owner_who_is_not_an_operator_can_neither_plan_nor_run_a_change(cow: FakeMailcow) -> None:
    import asyncio

    from anum_api.agent_tools import ToolCall, ToolPolicyOutcome
    from anum_api.model_gateway import MockModelGateway
    from anum_api.runtime import AgentRuntime
    from anum_api.mailbox.service import mail_tool_registry
    from anum_api.repository import InMemoryRepository

    call = ToolCall(name=domains_api.DOMAIN_TOOL, arguments={"operation": "add_domain", "domain": "new.test", "requested_by": "x"})
    runtime = AgentRuntime(MockModelGateway(), InMemoryRepository(store), tools=mail_tool_registry())
    # Planning: the runtime policy blocks it for a workspace owner, from any route or chat.
    assert runtime.evaluate(call, _context(OWNER_ONLY)).outcome == ToolPolicyOutcome.BLOCK
    assert runtime.evaluate(call, _context(MEMBER_OPERATOR)).outcome == ToolPolicyOutcome.REQUIRE_APPROVAL
    # Running: the handler refuses, even with a valid approved call.
    refused = asyncio.run(domains_api.change_mail_domains(call, _context(OWNER_ONLY)))
    assert refused.status == "failed" and refused.output["kind"] == "forbidden"
    # Deciding: an operator asks, a workspace owner who is not an operator approves; nothing is written.
    waiting = ask(operation="add_domain", domain="new.test").json()["approval"]
    client.post(f"/api/v1/approvals/{waiting['id']}/approve", headers=OWNER_ONLY, json={"payload_hash": waiting["payload_hash"]})
    assert cow.posts == []
    assert "new.test" not in cow.domains


def test_a_platform_operator_who_is_a_plain_member_may_ask_and_run(cow: FakeMailcow) -> None:
    import asyncio

    from anum_api.agent_tools import ToolCall

    assert client.get(BASE, headers=MEMBER_OPERATOR).status_code == 200
    asked = client.post(f"{BASE}/changes", headers=MEMBER_OPERATOR, json={"operation": "add_domain", "domain": "new.test"})
    assert asked.status_code == 200, asked.text
    call = ToolCall(name=domains_api.DOMAIN_TOOL, arguments=asked.json()["approval"]["arguments"])
    done = asyncio.run(domains_api.change_mail_domains(call, _context(MEMBER_OPERATOR)))
    assert done.status == "succeeded", done.summary
    assert [path for path, _, _ in cow.posts] == ["/api/v1/add/domain"]


# -- mailboxes ------------------------------------------------------------------------------------

FIRST_PASSWORD = "example-first-pass-42"


def _everything_stored() -> str:
    """Every audit record, event, approval and task the memory store holds, as one text."""
    parts: list[str] = []
    for name in ("audit_records", "events", "approvals", "tasks"):
        value = getattr(store, name)
        items = value.values() if isinstance(value, dict) else value
        parts.extend(item.model_dump_json() if hasattr(item, "model_dump_json") else json.dumps(item, default=str) for item in items)
    return "\n".join(parts)


def test_mailboxes_are_listed_per_domain(cow: FakeMailcow) -> None:
    body = client.get(BASE, headers=OPERATOR).json()
    assert body["mailboxes_read"] is True
    rows = {d["domain"]: d for d in body["domains"]}
    assert rows["example.test"]["mailbox_list"] == [
        {"address": "info@example.test", "name": "Front desk", "active": False, "quota_mb": None, "used_percent": None},
        {"address": "owner@example.test", "name": "Owner", "active": True, "quota_mb": 3072, "used_percent": 12.0},
    ]
    assert rows["new.test"]["mailbox_list"] == []


def test_adding_a_mailbox_waits_for_approval_and_never_shows_the_password(cow: FakeMailcow) -> None:
    waiting = ask(operation="add_mailbox", address="Team@Example.test", name="  Sales   team ", password=FIRST_PASSWORD, quota_mb=2048)
    assert waiting.status_code == 200, waiting.text
    assert FIRST_PASSWORD not in waiting.text
    approval = waiting.json()["approval"]
    args = approval["arguments"]
    assert args["operation"] == "add_mailbox" and args["address"] == "team@example.test" and args["name"] == "Sales team"
    assert args["quota_mb"] == 2048 and args["password_sealed"] == "[REDACTED]"
    assert cow.posts == []
    listed = client.get(BASE, headers=OPERATOR)
    assert FIRST_PASSWORD not in listed.text
    assert [(p["operation"], p["address"], p["name"], p["quota_mb"]) for p in listed.json()["pending"]] == [
        ("add_mailbox", "team@example.test", "Sales team", 2048)
    ]
    # The same mailbox again waits already, whatever password is typed.
    again = ask(operation="add_mailbox", address="team@example.test", name="Sales team", password="another-pass-77", quota_mb=2048)
    assert again.status_code == 409
    decided = approve(approval)
    assert decided.status_code == 200, decided.text
    assert FIRST_PASSWORD not in decided.text
    path, body, key = cow.posts[-1]
    assert (path, key) == ("/api/v1/add/mailbox", "example-write-key")
    assert body == {
        "local_part": "team", "domain": "example.test", "name": "Sales team", "password": FIRST_PASSWORD, "password2": FIRST_PASSWORD,
        "quota": "2048", "active": "1", "force_pw_update": "1", "tls_enforce_in": "1", "tls_enforce_out": "1",
    }
    after = client.get(BASE, headers=OPERATOR).json()
    rows = {d["domain"]: d for d in after["domains"]}
    assert "team@example.test" in [m["address"] for m in rows["example.test"]["mailbox_list"]] and after["pending"] == []
    # Nothing ANUM keeps holds the password in clear: audit, events, approvals, tasks.
    assert FIRST_PASSWORD not in _everything_stored()
    actions = [r.action for r in (store.audit_records.values() if isinstance(store.audit_records, dict) else store.audit_records)]
    assert "mail.domains.requested" in actions


def test_mailbox_input_is_checked_and_errors_never_echo_the_password(cow: FakeMailcow) -> None:
    long_secret = "x1" * 80
    cases = [
        ({"address": "team@example.test", "name": "Team", "password": "short1"}, "12 to 128"),
        ({"address": "team@example.test", "name": "Team", "password": long_secret}, "12 to 128"),
        ({"address": "team@example.test", "name": "Team", "password": "onlyletterspassword"}, "letters and digits"),
        ({"address": "team@example.test", "name": "Team", "password": "team-is-my-pass-1"}, "mailbox name"),
        ({"address": "team@example.test", "name": "   ", "password": FIRST_PASSWORD}, "name of the person"),
        ({"address": "team@example.test", "name": "Team", "password": FIRST_PASSWORD, "quota_mb": 10}, "256 to 102400"),
        ({"address": "no-at-sign", "name": "Team", "password": FIRST_PASSWORD}, "mailbox as name@domain"),
    ]
    for body, text in cases:
        response = ask(operation="add_mailbox", **body)
        assert response.status_code == 422, (body, response.text)
        assert text in response.text and body["password"] not in response.text
    # A password of the wrong type is refused without echoing the value either.
    typed = ask(operation="add_mailbox", address="team@example.test", name="Team", password=["example-in-a-list-9"])
    assert typed.status_code == 422 and "example-in-a-list-9" not in typed.text
    assert ask(operation="add_mailbox", address="owner@example.test", name="Owner", password=FIRST_PASSWORD).status_code == 409
    assert ask(operation="add_mailbox", address="sales@example.test", name="Sales", password=FIRST_PASSWORD).status_code == 409
    assert ask(operation="add_mailbox", address="team@new.test", name="Team", password=FIRST_PASSWORD).status_code == 409  # not in Mailcow
    assert ask(operation="add_mailbox", address="team@legacy.test", name="Team", password=FIRST_PASSWORD).status_code == 409  # not allowed
    cow.domains["example.test"]["max_num_mboxes_for_domain"] = 2
    full = ask(operation="add_mailbox", address="team@example.test", name="Team", password=FIRST_PASSWORD)
    assert full.status_code == 409 and "Raise the limit" in full.json()["error"]["message"]
    assert cow.posts == [] and len(store.approvals) == 0


def test_no_mailbox_is_added_while_the_mailbox_list_cannot_be_read(cow: FakeMailcow) -> None:
    cow.mailbox_read_status = 500
    body = client.get(BASE, headers=OPERATOR).json()
    assert body["mailcow_state"] == "ok" and body["mailboxes_read"] is False  # domains and aliases still show
    refused = ask(operation="add_mailbox", address="team@example.test", name="Team", password=FIRST_PASSWORD)
    assert refused.status_code == 409 and "mailbox list could not be read" in refused.json()["error"]["message"]


def test_an_approved_mailbox_is_checked_again_and_a_bad_seal_refuses(cow: FakeMailcow) -> None:
    import asyncio

    from anum_api.agent_tools import ToolCall

    waiting = ask(operation="add_mailbox", address="team@example.test", name="Team", password=FIRST_PASSWORD).json()["approval"]
    cow.mailboxes.append({"username": "team@example.test", "name": "Team", "active": "1"})  # made in Mailcow meanwhile
    approve(waiting)
    assert cow.posts == []
    call = ToolCall(name=domains_api.DOMAIN_TOOL, arguments={
        "operation": "add_mailbox", "domain": "example.test", "address": "desk@example.test", "name": "Desk", "quota_mb": 3072,
        "password_sealed": "not-a-sealed-value", "requested_by": "operator_dom",
    })
    refused = asyncio.run(domains_api.change_mail_domains(call, _context(OPERATOR)))
    assert refused.status == "failed" and refused.output["kind"] == "stale" and "Ask for the mailbox again" in refused.summary
    assert cow.posts == []


def test_mailcow_message_never_carries_the_password() -> None:
    def refusing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"type": "danger", "msg": ["password_complexity", FIRST_PASSWORD]}])

    cfg = mailcow.MailcowSettings(api_enabled=True, api_url="https://mail.example.test", write_enabled=True,
                                  api_write_key=mailcow.SecretStr("example-write-key"))
    original = mailcow.mailcow_transport
    mailcow.mailcow_transport = httpx.MockTransport(refusing)
    try:
        result = mailcow.add_mailbox("team@example.test", "Team", FIRST_PASSWORD, 3072, cfg)
    finally:
        mailcow.mailcow_transport = original
    assert result.ok is False and "password_complexity" in result.message and FIRST_PASSWORD not in result.message
