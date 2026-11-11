"""The ``web.act`` tool against a fake browser service (docs/action-browser.md).

Covers the kinds of task the action browser is for (sign in and fill a multi-step form, submit
with approval; sign in and download a statement into Files; compare data across two pages into
a table; a purchase that stops at payment), and its safety rules: the scope approval, gates
bound to the payload hash, the page-changed re-check, the domain list, handoffs, caps, Stop,
and secrets that never reach the model, the logs or an approval. The card security code is never
saved: the owner types it on the approval card and it is typed once, then dropped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Iterator, Mapping
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from action_browser_fakes import CARD, PASSWORD, FakeActionService, ScriptedPlanner
from anum_api import main
from anum_api.action_browser import ACT_TOOL, ACTION_INTEGRATION_ID
from anum_api.action_browser import api as action_api
from anum_api.action_browser.card_code import CVC_MARKER, with_card_code
from anum_api.action_browser.store import memory_action_store
from anum_api.action_browser.tool import ActionBrowserTool, ActionServiceClient, act_definition, merge_result, register_action_tools, table_csv
from anum_api.action_browser.vault import CredentialWrite, create_credential, update_credential
from anum_api.agent_planning import choose_tool_call
from anum_api.agent_skills import default_skill_registry
from anum_api.agent_tools import ToolCall, ToolPolicy, ToolPolicyOutcome, ToolResult, default_tool_registry, running_in
from anum_api.governance import governance_store
from anum_api.integrations import default_integration_registry
from anum_api.schemas import RiskLevel, TenantContext
from anum_api.settings import Settings, settings
from anum_api.store import store

TENANT = "tenant_act"
WORKSPACE = "workspace_act"
CONTEXT = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_a", roles=["owner"])
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_a", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_a", "x-user-roles": "member"}
TOKEN = "action-browser-token-for-tests-only-example"
# The security code the owner types on the approval card: a sentinel the tests look for everywhere it must not be.
CODE = "739"
CODE_TOKEN = re.compile(r"(?<![\w.])739(?![\w.])")
SCOPE = {"goal": "Book one seat to Riyadh", "start_url": "https://shop.example/login", "allowed_domains": ["shop.example"],
         "logins": {"shop": "shop.example"}, "cards": ["main"]}


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    memory_action_store.clear()
    yield
    memory_action_store.clear()


def save_secrets() -> None:
    create_credential(CONTEXT, CredentialWrite(kind="login", handle="shop", site="https://www.shop.example/login", username="owner@example.com", password=PASSWORD))
    create_credential(CONTEXT, CredentialWrite(kind="card", handle="main", name="Owner Name", number=CARD, expiry="08/29"))


def make_tool(service: FakeActionService, planner: ScriptedPlanner, **options: Any) -> ActionBrowserTool:
    client = ActionServiceClient("http://browser:8090", TOKEN, client=httpx.AsyncClient(transport=httpx.MockTransport(service)))
    return ActionBrowserTool(problem=None, client=client, planner_factory=lambda _context: planner, **options)


def call(tool: ActionBrowserTool, arguments: dict[str, Any], context: TenantContext = CONTEXT, *, code: str | None = None) -> ToolResult:
    async def go() -> ToolResult:
        with running_in("task_1", "run_1"):
            return await tool(ToolCall(name=ACT_TOOL, arguments=arguments), context)

    async def with_code() -> ToolResult:
        return await with_card_code(code, go()) if code is not None else await go()

    return asyncio.run(with_code())


def steps_of(session_id: str) -> list[Any]:
    return memory_action_store.steps(CONTEXT, session_id)


def booking_steps() -> list[dict[str, Any]]:
    return [
        {"action": "type", "ref": "e1", "value": "{{login:shop.username}}"},
        {"action": "type", "ref": "e2", "value": "{{login:shop.password}}"},
        {"action": "click", "ref": "e3", "reason": "Sign in"},
        {"action": "type", "ref": "e1", "value": "Owner Name"},
        {"action": "click", "ref": "e2"},
        {"action": "type", "ref": "e1", "value": "{{card:main.number}}"},
        {"action": "type", "ref": "e3", "value": "{{card:main.cvc}}"},
    ]


def test_a_booking_signs_in_fills_the_form_and_stops_at_card_entry_then_at_the_code_then_at_pay() -> None:
    save_secrets()
    service = FakeActionService()
    planner = ScriptedPlanner([*booking_steps(), {"action": "click", "ref": "e2"}, {"action": "done", "answer": "Booked, reference ABC123."}])
    tool = make_tool(service, planner)

    first = call(tool, SCOPE)
    assert first.status == "paused" and first.continuation is not None
    card = first.continuation.arguments
    assert card["kind"] == "card_entry" and card["resume"] == "step" and card["site"] == "shop.example" and "cvc" not in card
    assert card["shown_action"]["value"].startswith("{{card:main.number}} (Visa ending 4242")
    assert card["amount"] == 1240.0 and card["currency"] == "AED"
    assert first.continuation_reason.startswith("Waiting for your approval: enter")
    session_id = card["session_id"]

    # The security code is never saved, so it always asks, even inside the card form already approved.
    second = call(tool, card)
    assert second.status == "paused"
    code_step = second.continuation.arguments
    assert code_step["kind"] == "card_security_code" and code_step["cvc"] == CVC_MARKER and code_step["grant"] == card["grant"]
    assert code_step["action"]["value"] == "{{card:main.cvc}}" and "Type the card's security code" in second.continuation_reason

    third = call(tool, code_step, code=CODE)
    assert third.status == "paused"
    pay = third.continuation.arguments
    assert pay["kind"] == "binding_button" and pay["target"] == "“Pay AED 1,240”" and pay["amount_text"] == "AED 1,240"
    assert pay["url"] == "https://shop.example/pay" and len(pay["fingerprint"]) == 64 and "cvc" not in pay

    done = call(tool, pay)
    assert done.status == "succeeded" and done.output["answer"] == "Booked, reference ABC123."
    assert service.sessions == {} and service.closed == ["browser1"]
    record = memory_action_store.get_session(CONTEXT, session_id)
    assert record.status == "completed" and record.browser_id is None and record.task_id == "task_1"
    kinds = [step.kind for step in steps_of(session_id)]
    assert kinds[:5] == ["started", "opened", "typed", "typed", "clicked"] and "approval" in kinds and kinds[-1] == "done"

    # The secrets travelled only in the act request that typed them: never to the model, the log or an approval.
    secret_bodies = [body for _, path, body in service.requests if json.dumps(body).count(PASSWORD) or json.dumps(body).count(CARD)]
    assert len(secret_bodies) == 2 and all(set(body) == {"action", "expect", "secret"} for body in secret_bodies)
    code_bodies = [body for _, _, body in service.requests if CODE_TOKEN.search(json.dumps(body))]
    assert code_bodies == [{"action": code_step["action"], "expect": {"url": code_step["url"], "fingerprint": code_step["fingerprint"]}, "secret": CODE}]
    everything_else = "".join(planner.seen) + json.dumps([s.model_dump(mode="json") for s in steps_of(session_id)])
    everything_else += json.dumps(card) + json.dumps(code_step) + json.dumps(pay) + json.dumps(first.audit_metadata) + json.dumps(third.audit_metadata)
    everything_else += json.dumps(done.output) + record.model_dump_json() + done.summary
    assert PASSWORD not in everything_else and CARD not in everything_else and "4242 4242" not in everything_else
    assert not CODE_TOKEN.search(everything_else)


def test_the_code_is_never_read_from_the_vault_and_is_needed_at_approval() -> None:
    save_secrets()
    service = FakeActionService()
    tool = make_tool(service, ScriptedPlanner([*booking_steps(), {"action": "click", "ref": "e2"}]))
    card = call(tool, SCOPE).continuation.arguments
    code_step = call(tool, card).continuation.arguments
    # Approved without a code (for example from a client that cannot ask for it): nothing is typed, the task ends.
    without = call(tool, code_step)
    assert without.status == "failed" and "security code was not entered" in without.summary
    assert not [body for _, path, body in service.requests if path.endswith("/act") and body["action"].get("value") == "{{card:main.cvc}}"]


def test_the_page_changed_after_the_code_was_entered_drops_it_and_asks_again() -> None:
    save_secrets()
    service = FakeActionService()
    tool = make_tool(service, ScriptedPlanner([*booking_steps(), {"action": "click", "ref": "e2"}]))
    card = call(tool, SCOPE).continuation.arguments
    code_step = call(tool, card).continuation.arguments
    service.change_before_act = True
    again = call(tool, code_step, code=CODE)
    assert again.status == "paused"
    fresh = again.continuation.arguments
    assert fresh["kind"] == "card_security_code" and fresh["cvc"] == CVC_MARKER and fresh["pending_id"] != code_step["pending_id"]
    # The code went to the browser only in the act request the page refused; nothing was typed and it is not kept.
    assert service.sessions["browser1"]["values"].get(("/pay", "e3")) is None
    assert len([b for _, _, b in service.requests if CODE_TOKEN.search(json.dumps(b))]) == 1
    assert not CODE_TOKEN.search(json.dumps(fresh) + json.dumps([s.model_dump(mode="json") for s in steps_of(code_step["session_id"])]))
    # Approving the fresh step without typing the code again types nothing.
    assert call(tool, fresh).status == "failed"
    assert service.sessions == {}


def test_the_page_changed_after_approval_asks_again_and_does_nothing() -> None:
    save_secrets()
    service = FakeActionService()
    tool = make_tool(service, ScriptedPlanner([*booking_steps(), {"action": "click", "ref": "e2"}]))
    card = call(tool, SCOPE).continuation.arguments
    code_step = call(tool, card).continuation.arguments
    pay = call(tool, code_step, code=CODE).continuation.arguments
    service.change_before_act = True
    again = call(tool, pay)
    assert again.status == "paused"
    fresh = again.continuation.arguments
    assert fresh["amount_text"] == "AED 1,990" and fresh["fingerprint"] != pay["fingerprint"] and fresh["pending_id"] != pay["pending_id"]
    assert service.sessions["browser1"].get("paid_with") is None
    assert any(step.kind == "page_changed" for step in steps_of(pay["session_id"]))
    # The old approval cannot be replayed: it is not the pending step any more.
    stale = call(tool, pay)
    assert stale.status == "failed" and "not the one" in stale.summary


def test_a_saved_card_never_holds_a_security_code() -> None:
    from anum_api.action_browser.handles import Handle
    from anum_api.action_browser.vault import resolve_secret
    from anum_api.secret_box import secret_cipher

    with pytest.raises(ValueError, match="never stores a card's security code") as refused:
        CredentialWrite(kind="card", handle="main", name="Owner Name", number=CARD, expiry="08/29", cvc=CODE)  # type: ignore[call-arg]
    assert not CODE_TOKEN.search(str(refused.value))
    for key in ("CVV", "csc", "security_code"):
        with pytest.raises(ValueError):
            CredentialWrite.model_validate({"kind": "card", "handle": "main", "number": CARD, "expiry": "08/29", key: CODE})
    created = create_credential(CONTEXT, CredentialWrite(kind="card", handle="main", name="Owner Name", number=CARD, expiry="08/29"))
    stored = memory_action_store.credential(CONTEXT, created.id)
    assert json.loads(secret_cipher().decrypt(stored.secret_ciphertext)) == {"number": CARD, "expiry": "08/29", "name": "Owner Name"}
    update_credential(CONTEXT, created.id, CredentialWrite(kind="card", handle="main", expiry="09/30"))
    stored = memory_action_store.credential(CONTEXT, created.id)
    assert json.loads(secret_cipher().decrypt(stored.secret_ciphertext)) == {"number": CARD, "expiry": "09/30", "name": "Owner Name"}
    assert resolve_secret(CONTEXT, Handle("card", "main", "cvc")) is None and resolve_secret(CONTEXT, Handle("card", "main", "number")) == CARD


def test_a_card_saved_with_a_code_before_loses_it(caplog: pytest.LogCaptureFixture) -> None:
    from anum_api.action_browser.cleanup import strip_saved_card_codes
    from anum_api.action_browser.handles import Handle
    from anum_api.action_browser.vault import resolve_secret
    from anum_api.secret_box import secret_cipher

    created = create_credential(CONTEXT, CredentialWrite(kind="card", handle="main", name="Owner Name", number=CARD, expiry="08/29"))
    login = create_credential(CONTEXT, CredentialWrite(kind="login", handle="shop", site="shop.example", username="o@example.com", password=PASSWORD))
    # A row as the first version of the vault wrote it.
    old = memory_action_store.credential(CONTEXT, created.id)
    legacy = {"number": CARD, "expiry": "08/29", "cvc": CODE, "name": "Owner Name"}
    memory_action_store.save_credential(CONTEXT, old.model_copy(update={"secret_ciphertext": secret_cipher().encrypt(json.dumps(legacy))}))
    # Even before the cleanup runs, the code is never handed out.
    assert resolve_secret(CONTEXT, Handle("card", "main", "cvc")) is None
    store.audit_records.clear()
    with caplog.at_level(logging.DEBUG):
        report = strip_saved_card_codes()
        again = strip_saved_card_codes()
    assert (report.scanned, report.removed, report.failed) == (2, 1, 0) and again.removed == 0
    after = memory_action_store.credential(CONTEXT, created.id)
    assert json.loads(secret_cipher().decrypt(after.secret_ciphertext)) == {"number": CARD, "expiry": "08/29", "name": "Owner Name"}
    assert after.updated_at == old.updated_at and memory_action_store.credential(CONTEXT, login.id).secret_ciphertext
    (audit,) = [r for r in store.audit_records if r.action == "action_browser.card_code_removed"]
    assert dict(audit.metadata) == {"kind": "card", "handle": "main", "site": None} and audit.target == f"action_browser_credential:{created.id}"
    assert not CODE_TOKEN.search(caplog.text + json.dumps(dict(audit.metadata)))


def test_a_multi_step_form_is_submitted_only_after_approval() -> None:
    save_secrets()
    service = FakeActionService()
    goal = {**SCOPE, "goal": "Fill the visa form", "start_url": "https://shop.example/visa", "cards": []}
    planner = ScriptedPlanner([
        {"action": "type", "ref": "e1", "value": "Owner Name"}, {"action": "click", "ref": "e2"},
        {"action": "type", "ref": "e1", "value": "P1234567"}, {"action": "click", "ref": "e2"},
        {"action": "extract", "data": {"fields": {"Application number": "V-778"}}},
        {"action": "done", "answer": "Submitted the visa application."},
    ])
    tool = make_tool(service, planner)
    paused = call(tool, goal)
    submit = paused.continuation.arguments
    assert submit["kind"] == "binding_button" and "Submit application" in submit["target"]
    assert not service.sessions["browser1"].get("submitted")
    done = call(tool, submit)
    assert done.status == "succeeded" and service.closed == ["browser1"]
    assert done.output["fields"] == {"Application number": "V-778"} and "Application number: V-778" in done.summary


def test_sign_in_and_download_a_statement_into_files() -> None:
    from anum_api.files import open_file_metadata_store

    save_secrets()
    service = FakeActionService()
    planner = ScriptedPlanner([
        {"action": "type", "ref": "e1", "value": "{{login:shop.username}}"}, {"action": "type", "ref": "e2", "value": "{{login:shop.password}}"},
        {"action": "click", "ref": "e3"}, {"action": "navigate", "url": "https://shop.example/statements"},
        {"action": "click", "ref": "e1"}, {"action": "click", "ref": "e2"},
        {"action": "done", "answer": "Downloaded the October statement."},
    ])
    done = call(make_tool(service, planner), {**SCOPE, "goal": "Download my October statement", "cards": []})
    assert done.status == "succeeded"
    (saved,) = done.output["files"]
    assert saved["name"] == "oct.pdf"
    with open_file_metadata_store(CONTEXT) as files:
        record = files.get(CONTEXT, saved["id"])
    assert record is not None and record.content_type == "application/pdf" and record.workspace_id == WORKSPACE
    assert "Saved oct.pdf to Files" in [s.summary for s in steps_of(done.output["session_id"])]


def test_compare_two_pages_into_a_table_saved_as_csv() -> None:
    service = FakeActionService()
    planner = ScriptedPlanner([
        {"action": "navigate", "url": "https://shop.example/prices/a"},
        {"action": "extract", "data": {"columns": ["Supplier", "Cement 50kg"], "rows": [["A", "AED 21"]]}},
        {"action": "navigate", "url": "https://shop.example/prices/b"},
        {"action": "extract", "data": {"rows": [["B", "AED 19"]]}},
        {"action": "done", "answer": "Supplier B is cheaper.", "save_as": "csv"},
    ])
    done = call(make_tool(service, planner), {**SCOPE, "goal": "Compare cement prices", "start_url": "https://shop.example/prices/a", "logins": {}, "cards": []})
    assert done.status == "succeeded"
    assert done.output["columns"] == ["Supplier", "Cement 50kg"] and done.output["rows"] == [["A", "AED 21"], ["B", "AED 19"]]
    assert "| A | AED 21 |" in done.summary and done.output["files"][0]["name"].endswith(".csv")


def test_results_are_capped_and_csv_is_safe_in_spreadsheets() -> None:
    result: dict[str, Any] = {}
    assert merge_result(result, {"columns": ["a"], "rows": [["=HYPERLINK(1)"]] * 500}) == 200
    assert table_csv(result).splitlines()[1] == "'=HYPERLINK(1)"


def test_leaving_the_sites_asks_and_the_owner_adds_one_site() -> None:
    service = FakeActionService()
    planner = ScriptedPlanner([{"action": "navigate", "url": "https://partner.example/offer"}, {"action": "done", "answer": "ok"}])
    tool = make_tool(service, planner)
    paused = call(tool, {**SCOPE, "logins": {}, "cards": []})
    args = paused.continuation.arguments
    assert args["resume"] == "off_site" and args["site"] == "partner.example"
    assert "outside the approved list" in paused.continuation_reason
    assert call(tool, args).status == "succeeded"
    record = memory_action_store.get_session(CONTEXT, args["session_id"])
    assert "partner.example" in record.sites


def test_a_captcha_hands_over_to_the_owner_and_continues_after() -> None:
    service = FakeActionService()
    service.site.captcha_on = "/login"
    tool = make_tool(service, ScriptedPlanner([{"action": "done", "answer": "Signed in."}]))
    paused = call(tool, {**SCOPE, "logins": {}, "cards": []})
    args = paused.continuation.arguments
    assert args["resume"] == "handoff" and args["check"] == "captcha" and paused.continuation_reason.startswith("Needs you:")
    record = memory_action_store.get_session(CONTEXT, args["session_id"])
    assert record.status == "waiting" and record.waiting == "handoff"
    service.site.captcha_on = None
    assert call(tool, args).status == "succeeded"


def test_refusals_never_reach_the_browser_and_too_many_end_the_task() -> None:
    service = FakeActionService()
    planner = ScriptedPlanner([{"action": "type", "ref": "e1", "value": "4242 4242 4242 4242"}] * 6)
    done = call(make_tool(service, planner), {**SCOPE, "logins": {}, "cards": []})
    assert done.status == "failed" and "refuses" in done.summary
    assert not any(path.endswith("/act") and body["action"].get("type") == "type" for _, path, body in service.requests)


def test_step_time_and_daily_caps() -> None:
    service = FakeActionService()
    looping = ScriptedPlanner([{"action": "scroll"}] * 20)
    capped = call(make_tool(service, looping, max_steps=5), {**SCOPE, "logins": {}, "cards": []})
    assert capped.status == "failed" and "5 steps" in capped.summary and capped.audit_metadata["outcome"] == "step_cap"

    ticks = iter(range(0, 10_000, 100))
    slow = call(make_tool(FakeActionService(), ScriptedPlanner([{"action": "scroll"}] * 20), segment_seconds=250, clock=lambda: float(next(ticks))),
                {**SCOPE, "logins": {}, "cards": []})
    assert slow.status == "paused" and slow.continuation.arguments["resume"] == "continue"

    memory_action_store.clear()
    tool = make_tool(FakeActionService(), ScriptedPlanner([{"action": "done"}] * 5), tasks_per_day=2)
    assert call(tool, {**SCOPE, "logins": {}, "cards": []}).status == "succeeded"
    assert call(tool, {**SCOPE, "logins": {}, "cards": []}).status == "succeeded"
    third = call(tool, {**SCOPE, "logins": {}, "cards": []})
    assert third.status == "failed" and third.audit_metadata["outcome"] == "daily_cap"


def test_availability_switch_scope_and_run_context() -> None:
    service = FakeActionService()
    off = ActionBrowserTool(problem="Not available on this server: x", client=None, planner_factory=lambda c: ScriptedPlanner([]))
    assert call(off, SCOPE).audit_metadata["outcome"] == "unavailable"
    disabled = make_tool(service, ScriptedPlanner([]), enabled=lambda _c: False)
    assert call(disabled, SCOPE).audit_metadata["outcome"] == "workspace_disabled" and service.requests == []
    tool = make_tool(service, ScriptedPlanner([]))
    assert call(tool, {**SCOPE, "start_url": "https://other.example/"}).audit_metadata["outcome"] == "refused_scope"
    assert call(tool, {**SCOPE, "start_url": "http://shop.example/"}).audit_metadata["outcome"] == "refused_url"
    assert call(tool, {**SCOPE, "start_url": "https://127.0.0.1/"}).audit_metadata["outcome"] == "refused_url"
    assert call(tool, {**SCOPE, "logins": {"bank": "bank.example"}}).audit_metadata["outcome"] == "refused_scope"
    outside = asyncio.run(tool(ToolCall(name=ACT_TOOL, arguments=SCOPE), CONTEXT))
    assert outside.audit_metadata["outcome"] == "no_task"
    service.no_action_mode = True
    assert "action mode" in call(tool, SCOPE).summary
    service.no_action_mode, service.busy = False, True
    assert call(tool, SCOPE).audit_metadata["outcome"] == "busy"


def test_a_session_from_another_workspace_is_not_resumed() -> None:
    service = FakeActionService()
    tool = make_tool(service, ScriptedPlanner([{"action": "navigate", "url": "https://partner.example/"}]))
    args = call(tool, {**SCOPE, "logins": {}, "cards": []}).continuation.arguments
    other = CONTEXT.model_copy(update={"workspace_id": "workspace_other"})
    assert call(tool, args, other).audit_metadata["outcome"] == "unknown_session"


def test_the_definition_is_high_risk_owner_only_and_always_asks() -> None:
    definition = act_definition()
    assert definition.risk_level == RiskLevel.HIGH and definition.always_ask and definition.long_running
    assert definition.required_roles == frozenset({"owner"})
    registry = register_action_tools(default_tool_registry(None, None), settings, None, lambda c: ScriptedPlanner([]))
    decision = ToolPolicy(registry.names).evaluate(ToolCall(name=ACT_TOOL, arguments=SCOPE), registry.definition(ACT_TOOL), CONTEXT)
    assert decision.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL and not decision.lowerable
    member = CONTEXT.model_copy(update={"roles": ["member"]})
    assert ToolPolicy(registry.names).evaluate(ToolCall(name=ACT_TOOL, arguments=SCOPE), registry.definition(ACT_TOOL), member).outcome == ToolPolicyOutcome.BLOCK


@pytest.mark.parametrize("prompt", [
    "Book me Dubai to Riyadh on Friday morning on https://www.example-air.com",
    "Log into https://portal.example.gov and fill the visa form",
    "Download my October statement from https://bank.example",
    "Compare cement prices on https://a.example and https://b.example and collect them in a table",
    "Check the status of order 123 on https://shop.example",
    "تحقق من حالة الشحنة على https://track.example",
])
def test_the_goal_and_sites_come_from_the_users_own_words(prompt: str) -> None:
    registry = register_action_tools(default_tool_registry(None, None), settings, None, lambda c: ScriptedPlanner([]))
    skills = default_skill_registry().select(prompt, registry.names)
    chosen = choose_tool_call(prompt, skills, "model text with https://evil.example", registry.names)
    assert chosen.name == ACT_TOOL and chosen.arguments["start_url"].startswith("https://")
    assert "evil.example" not in json.dumps(chosen.arguments)
    # Reading a page stays with the read-only browser.
    assert "anum.action-browser" not in [s.id for s in default_skill_registry().select("Open https://site.example/pricing and read the page", registry.names)]


def test_settings_refuse_the_action_browser_without_the_browser_service() -> None:
    with pytest.raises(ValueError, match="ANUM_ACTION_BROWSER_ENABLED"):
        Settings(environment="test", action_browser_enabled=True, browser_research_url="", browser_research_token="")
    assert Settings(environment="test", action_browser_enabled=True, browser_research_url="http://browser:8090", browser_research_token=TOKEN).action_browser_enabled


# -- through the API: approvals with the payload hash, the live view, Stop, the vault ----------


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[FakeActionService, dict[str, Any]]]:
    service = FakeActionService()
    configured = settings.model_copy(update={"browser_research_url": "http://browser:8090", "browser_research_token": TOKEN, "action_browser_enabled": True})
    integrations = default_integration_registry(configured)
    holder: dict[str, Any] = {"planner": ScriptedPlanner([])}
    client = ActionServiceClient("http://browser:8090", TOKEN, client=None)
    client._client = None
    transport = httpx.MockTransport(service)

    class Client(ActionServiceClient):
        async def _request(self, method: str, path: str, body: Any = None, *, picture: bool = False) -> Any:  # type: ignore[override]
            self._client = httpx.AsyncClient(transport=transport)
            try:
                return await ActionServiceClient._request(self, method, path, body, picture=picture)
            finally:
                await self._client.aclose()
                self._client = None

        async def download(self, browser_id: str, download_id: str) -> Any:  # type: ignore[override]
            self._client = httpx.AsyncClient(transport=transport)
            try:
                return await ActionServiceClient.download(self, browser_id, download_id)
            finally:
                await self._client.aclose()
                self._client = None

    shared = Client("http://browser:8090", TOKEN)
    registry = default_tool_registry(None, None)
    registry.register(act_definition(), ActionBrowserTool(problem=None, client=shared, planner_factory=lambda c: holder["planner"],
                                                          enabled=integrations.workspace_enabled(ACTION_INTEGRATION_ID)))
    monkeypatch.setattr(main, "integration_registry", integrations)
    monkeypatch.setattr(main, "tool_registry", registry)
    monkeypatch.setattr(action_api, "service_client", shared)
    governance_store.clear()
    for collection in (store.tasks, store.runs, store.approvals, store.events, store.audit_records):
        collection.clear()
    yield service, holder
    governance_store.clear()
    monkeypatch.setattr(action_api, "service_client", None)


def _approve(client: TestClient, approval: dict[str, Any], headers: dict[str, str] = OWNER, *, code: str | None = None) -> httpx.Response:
    body = {"payload_hash": approval["payload_hash"], **({"security_code": code} if code is not None else {})}
    return client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=headers, json=body)


def _everything_stored() -> str:
    """Every row the in-memory backend keeps (tasks, runs, approvals, events, audit, sessions, steps, saved items)."""
    import dataclasses

    rows: list[Any] = [*store.tasks.values(), *store.runs.values(), *store.approvals.values(), *store.events, *store.audit_records]
    dumped = [row.model_dump(mode="json") if hasattr(row, "model_dump")
              else {f.name: (dict(v) if isinstance(v := getattr(row, f.name), Mapping) else v) for f in dataclasses.fields(row)} for row in rows]
    sessions = [r for (t, w, _), r in memory_action_store._sessions.items() if (t, w) == (TENANT, WORKSPACE)]
    dumped += [r.model_dump(mode="json") for r in sessions]
    dumped += [step.model_dump(mode="json") for r in sessions for step in memory_action_store.steps(CONTEXT, r.id)]
    from anum_api.secret_box import secret_cipher

    dumped += [secret_cipher().decrypt(r.secret_ciphertext) for r in memory_action_store.credentials(CONTEXT)]
    return json.dumps(dumped, default=str)


def _wait_pending(client: TestClient, task_id: str, count: int, timeout: float = 15.0) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = [a for a in client.get("/api/v1/approvals", headers=OWNER).json() if a["task_id"] == task_id and a["status"] == "pending"]
        task = client.get(f"/api/v1/tasks/{task_id}", headers=OWNER).json()
        if len(pending) >= count or task["status"] in {"completed", "failed", "cancelled"}:
            return pending
        time.sleep(0.05)
    raise AssertionError("timed out")


def _wait_task(client: TestClient, task_id: str, status: str, timeout: float = 15.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = client.get(f"/api/v1/tasks/{task_id}", headers=OWNER).json()
        if task["status"] == status:
            return task
        time.sleep(0.05)
    raise AssertionError(f"task stayed {task['status']}")


def _turn_on(client: TestClient) -> None:
    assert client.put(f"/api/v1/integrations/{ACTION_INTEGRATION_ID}/configuration", headers=MEMBER, json={"enabled": True}).status_code == 403
    assert client.put(f"/api/v1/integrations/{ACTION_INTEGRATION_ID}/configuration", headers=OWNER, json={"enabled": True}).status_code == 200


def _start(client: TestClient, prompt: str) -> tuple[str, dict[str, Any]]:
    task = client.post("/api/v1/tasks", headers=OWNER, json={"title": "Browser task", "prompt": prompt}).json()
    started = client.post(f"/api/v1/tasks/{task['id']}/run", headers=OWNER).json()
    assert started["approval"] is not None and started["approval"]["action"] == ACT_TOOL
    return task["id"], started["approval"]


def test_the_api_path_scope_approval_pay_approval_live_view_and_completion(api: Any) -> None:
    service, holder = api
    with TestClient(main.app) as client:
        _turn_on(client)
        assert client.post("/api/v1/action-browser/credentials", headers=MEMBER, json={"kind": "card", "handle": "main"}).status_code == 403
        login = client.post("/api/v1/action-browser/credentials", headers=OWNER, json={
            "kind": "login", "handle": "shop", "site": "shop.example", "username": "owner@example.com", "password": PASSWORD})
        assert login.status_code == 201 and login.json()["hint"] == "o***@example.com on shop.example" and PASSWORD not in login.text
        with_code = client.post("/api/v1/action-browser/credentials", headers=OWNER, json={
            "kind": "card", "handle": "main", "name": "Owner Name", "number": CARD, "expiry": "08/29", "cvc": CODE})
        assert with_code.status_code == 422 and "never stores a card's security code" in with_code.text and not CODE_TOKEN.search(with_code.text)
        card = client.post("/api/v1/action-browser/credentials", headers=OWNER, json={
            "kind": "card", "handle": "main", "name": "Owner Name", "number": CARD, "expiry": "08/29"})
        assert card.status_code == 201 and card.json()["hint"] == "Visa ending 4242, expires 08/29"
        edit_code = client.put(f"/api/v1/action-browser/credentials/{card.json()['id']}", headers=OWNER, json={"kind": "card", "handle": "main", "cvc": CODE})
        assert edit_code.status_code == 422 and "never stores" in edit_code.text
        bad = client.post("/api/v1/action-browser/credentials", headers=OWNER, json={"kind": "card", "handle": "x", "name": "a", "number": "4242424242424241", "expiry": "08/29"})
        assert bad.status_code == 422
        listed = client.get("/api/v1/action-browser/credentials", headers=OWNER)
        assert CARD not in listed.text and PASSWORD not in listed.text and len(listed.json()) == 2

        holder["planner"] = ScriptedPlanner([*booking_steps(), {"action": "click", "ref": "e2"}, {"action": "done", "answer": "Booked, reference ABC123."}])
        task_id, scope = _start(client, "Book one seat on https://shop.example/login and pay with my main card")
        assert scope["arguments"]["allowed_domains"] == ["shop.example"] and scope["arguments"]["logins"] == {"shop": "shop.example"}
        assert scope["arguments"]["cards"] == ["main"] and scope["risk_level"] == "high"
        assert client.post(f"/api/v1/approvals/{scope['id']}/approve", headers=OWNER, json={"payload_hash": "0" * 64}).status_code == 409
        assert _approve(client, scope).status_code == 200

        (card_step,) = _wait_pending(client, task_id, 1)
        assert card_step["arguments"]["kind"] == "card_entry" and card_step["reason"].startswith("Waiting for your approval")
        sessions = client.get(f"/api/v1/action-browser/sessions?task_id={task_id}", headers=OWNER).json()
        (session,) = sessions
        assert session["status"] == "waiting" and session["waiting"] == "approval" and session["live"] and session["can_watch"]
        assert "browser_id" not in json.dumps(session) and "browser1" not in json.dumps(session)
        frame = client.get(f"/api/v1/action-browser/sessions/{session['id']}/frame", headers=OWNER)
        assert frame.status_code == 200 and frame.headers["content-type"] == "image/jpeg"
        assert client.get(f"/api/v1/action-browser/sessions/{session['id']}/frame", headers=MEMBER).status_code == 403
        member_view = client.get(f"/api/v1/action-browser/sessions/{session['id']}", headers=MEMBER).json()
        assert member_view["can_watch"] is False and member_view["steps"]
        # Input only while the browser needs a person.
        assert client.post(f"/api/v1/action-browser/sessions/{session['id']}/input", headers=OWNER, json={"kind": "click", "x": 10, "y": 10}).status_code == 409

        # A step that does not type the code takes none.
        assert _approve(client, card_step, code=CODE).status_code == 422
        assert _approve(client, card_step).status_code == 200
        (code_step,) = [a for a in _wait_pending(client, task_id, 1) if a["id"] != card_step["id"]]
        assert code_step["arguments"]["kind"] == "card_security_code" and code_step["arguments"]["cvc"] == CVC_MARKER
        # The code must come with the approve, as 3 or 4 digits; a refusal leaves the approval waiting.
        missing = _approve(client, code_step)
        assert missing.status_code == 422 and "security code" in missing.json()["error"]["message"]
        assert _approve(client, code_step, code="12a").status_code == 422
        assert _approve(client, code_step, code="12345").status_code == 422
        assert next(a for a in client.get("/api/v1/approvals", headers=OWNER).json() if a["id"] == code_step["id"])["status"] == "pending"
        assert _approve(client, code_step, code=CODE).status_code == 200
        (pay,) = [a for a in _wait_pending(client, task_id, 1) if a["id"] not in {card_step["id"], code_step["id"]}]
        assert pay["arguments"]["amount"] == 1240.0 and pay["arguments"]["currency"] == "AED"
        assert _approve(client, pay).status_code == 200
        task = _wait_task(client, task_id, "completed")
        assert task["status"] == "completed"
        final = client.get(f"/api/v1/action-browser/sessions/{session['id']}", headers=OWNER).json()
        assert final["status"] == "completed" and not final["live"]
        audits = [r for r in store.audit_records if r.action == "tool.call"]
        assert audits and PASSWORD not in json.dumps([dict(r.metadata) for r in audits], default=str) and CARD not in json.dumps([dict(r.metadata) for r in audits], default=str)
        assert service.closed == ["browser1"]
        # The code was typed once, in the act request for the approved field, and is nowhere else.
        code_bodies = [body for _, path, body in service.requests if CODE_TOKEN.search(json.dumps(body))]
        assert len(code_bodies) == 1 and code_bodies[0]["secret"] == CODE and code_bodies[0]["action"]["value"] == "{{card:main.cvc}}"
        assert not CODE_TOKEN.search(_everything_stored() + json.dumps(task, default=str))


def test_the_api_path_reject_ends_the_task_and_closes_the_browser(api: Any) -> None:
    service, holder = api
    with TestClient(main.app) as client:
        _turn_on(client)
        holder["planner"] = ScriptedPlanner([{"action": "navigate", "url": "https://shop.example/pay"}, {"action": "click", "ref": "e2"}])
        task_id, scope = _start(client, "Pay my bill on https://shop.example/login")
        assert _approve(client, scope).status_code == 200
        (pay,) = _wait_pending(client, task_id, 1)
        assert client.post(f"/api/v1/approvals/{pay['id']}/reject", headers=OWNER, json={"reason": "Not now"}).status_code == 200
        _wait_task(client, task_id, "failed")
        (session,) = client.get(f"/api/v1/action-browser/sessions?task_id={task_id}", headers=OWNER).json()
        assert session["status"] == "failed" and not session["live"] and service.closed == ["browser1"]
        assert "paid_with" not in json.dumps(list(service.sessions.values()))


def test_the_api_path_card_code_reaches_the_browser_once_and_is_kept_nowhere(api: Any, caplog: pytest.LogCaptureFixture) -> None:
    service, holder = api
    save_secrets()
    with TestClient(main.app) as client, caplog.at_level(logging.DEBUG):
        _turn_on(client)
        holder["planner"] = ScriptedPlanner([{"action": "navigate", "url": "https://shop.example/pay"},
                                             {"action": "type", "ref": "e3", "value": "{{card:main.cvc}}"},
                                             {"action": "done", "answer": "Code entered."}])
        task_id, scope = _start(client, "Pay my bill on https://shop.example/login with my main card")
        assert _approve(client, scope).status_code == 200
        (code_step,) = _wait_pending(client, task_id, 1)
        assert code_step["arguments"]["cvc"] == CVC_MARKER and "Type the card's security code" in code_step["reason"]
        assert _approve(client, code_step, code=CODE).status_code == 200
        task = _wait_task(client, task_id, "completed")
        events = client.get(f"/api/v1/tasks/{task_id}/events", headers=OWNER)
        acts = [body for _, path, body in service.requests if path.endswith("/act") and body.get("secret") == CODE]
        assert len(acts) == 1 and acts[0]["action"] == code_step["arguments"]["action"]
        assert not CODE_TOKEN.search(_everything_stored() + json.dumps(task, default=str) + events.text + caplog.text)


def test_the_api_path_reject_drops_the_code_step(api: Any) -> None:
    service, holder = api
    save_secrets()
    with TestClient(main.app) as client:
        _turn_on(client)
        holder["planner"] = ScriptedPlanner([{"action": "navigate", "url": "https://shop.example/pay"},
                                             {"action": "type", "ref": "e3", "value": "{{card:main.cvc}}"}])
        task_id, scope = _start(client, "Pay my bill on https://shop.example/login with my main card")
        assert _approve(client, scope).status_code == 200
        (code_step,) = _wait_pending(client, task_id, 1)
        assert client.post(f"/api/v1/approvals/{code_step['id']}/reject", headers=OWNER, json={"payload_hash": code_step["payload_hash"]}).status_code == 200
        _wait_task(client, task_id, "failed")
        (session,) = client.get(f"/api/v1/action-browser/sessions?task_id={task_id}", headers=OWNER).json()
        assert session["status"] == "failed" and not session["live"] and service.closed == ["browser1"]
        assert not [b for _, path, b in service.requests if path.endswith("/act") and b["action"].get("value") == "{{card:main.cvc}}"]


def test_the_api_path_stop_and_handoff_input(api: Any) -> None:
    service, holder = api
    service.site.captcha_on = "/login"
    with TestClient(main.app) as client:
        _turn_on(client)
        task_id, scope = _start(client, "Log in on https://shop.example/login and check my order status")
        assert _approve(client, scope).status_code == 200
        (handoff,) = _wait_pending(client, task_id, 1)
        assert handoff["reason"].startswith("Needs you:")
        (session,) = client.get(f"/api/v1/action-browser/sessions?task_id={task_id}", headers=OWNER).json()
        assert session["waiting"] == "handoff"
        assert client.post(f"/api/v1/action-browser/sessions/{session['id']}/input", headers=MEMBER, json={"kind": "text", "text": "x"}).status_code == 403
        assert client.post(f"/api/v1/action-browser/sessions/{session['id']}/input", headers=OWNER, json={"kind": "click", "x": 100, "y": 200}).status_code == 200
        assert service.inputs == [{"kind": "click", "x": 100.0, "y": 200.0}]
        stopped = client.post(f"/api/v1/action-browser/sessions/{session['id']}/stop", headers=OWNER)
        assert stopped.status_code == 200 and stopped.json()["status"] == "stopped"
        assert client.get(f"/api/v1/tasks/{task_id}", headers=OWNER).json()["status"] == "cancelled"
        assert service.closed == ["browser1"]
        expired = [a for a in client.get("/api/v1/approvals", headers=OWNER).json() if a["id"] == handoff["id"]]
        assert expired[0]["status"] == "expired"
        assert any(r.action == "action_browser.session_stopped" for r in store.audit_records)
        assert any(r.action == "action_browser.input" for r in store.audit_records)


def test_credentials_edit_keeps_secrets_and_delete(api: Any) -> None:
    with TestClient(main.app) as client:
        created = client.post("/api/v1/action-browser/credentials", headers=OWNER, json={
            "kind": "login", "handle": "shop", "site": "shop.example", "username": "owner@example.com", "password": PASSWORD}).json()
        dup = client.post("/api/v1/action-browser/credentials", headers=OWNER, json={
            "kind": "login", "handle": "shop", "site": "shop.example", "username": "x@example.com", "password": "y"})
        assert dup.status_code == 422
        edited = client.put(f"/api/v1/action-browser/credentials/{created['id']}", headers=OWNER, json={"kind": "login", "handle": "shop", "username": "new@example.com"})
        assert edited.status_code == 200 and edited.json()["hint"] == "n***@example.com on shop.example"
        from anum_api.action_browser.handles import Handle
        from anum_api.action_browser.vault import resolve_secret

        assert resolve_secret(CONTEXT, Handle("login", "shop", "password")) == PASSWORD
        assert client.delete(f"/api/v1/action-browser/credentials/{created['id']}", headers=MEMBER).status_code == 403
        assert client.delete(f"/api/v1/action-browser/credentials/{created['id']}", headers=OWNER).status_code == 204
        assert client.get("/api/v1/action-browser/credentials", headers=OWNER).json() == []
        actions = [r.action for r in store.audit_records if r.action.startswith("action_browser.credential")]
        assert actions == ["action_browser.credential_created", "action_browser.credential_updated", "action_browser.credential_deleted"]


def test_the_runs_list_pages_newest_first_and_hides_private_tasks(api: Any) -> None:
    """``GET /runs``: the phone's runs list (docs/action-browser.md#api)."""
    from datetime import UTC, datetime, timedelta

    from anum_api.action_browser.store import SessionRecord
    from anum_api.schemas import TaskStatus

    with TestClient(main.app) as client:
        made = [client.post("/api/v1/tasks", headers=OWNER, json={"title": f"Browser task {n}", "prompt": f"Check https://shop.example/{n}"}).json()
                for n in range(4)]
        store.tasks[made[3]["id"]].private_to = "owner_a"  # the owner's private task: a member never sees its run
        store.tasks[made[0]["id"]].status = TaskStatus.COMPLETED
        start = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
        for n, task in enumerate(made):
            memory_action_store.create_session(CONTEXT, SessionRecord(
                id=f"abs_{n}", task_id=task["id"], run_id=f"run_{n}", created_by="owner_a", goal=f"Check order {n}",
                start_url=f"https://shop.example/{n}", sites=["shop.example"], created_at=start + timedelta(minutes=n),
                updated_at=start + timedelta(minutes=n)))
        other = TenantContext(tenant_id=TENANT, workspace_id="workspace_other", user_id="owner_a", roles=["owner"])
        memory_action_store.create_session(other, SessionRecord(id="abs_other", task_id="task_other", run_id="run_x", created_by="owner_a",
                                                                goal="Elsewhere", start_url="https://x.example", created_at=start, updated_at=start))

        first = client.get("/api/v1/action-browser/runs?limit=2", headers=OWNER)
        assert first.status_code == 200
        body = first.json()
        assert [r["id"] for r in body["items"]] == ["abs_3", "abs_2"] and body["next_offset"] == 2
        assert body["items"][0]["steps"] == [] and body["items"][0]["can_stop"] is True and body["items"][0]["can_watch"] is True
        last = client.get("/api/v1/action-browser/runs?limit=2&offset=2", headers=OWNER).json()
        assert [r["id"] for r in last["items"]] == ["abs_1", "abs_0"] and last["next_offset"] is None
        # A session whose task already ended is settled when listed, as the per-task read does.
        assert last["items"][1]["status"] == "failed" and last["items"][1]["outcome"] == "task_ended"

        member = client.get("/api/v1/action-browser/runs?limit=2", headers=MEMBER).json()
        assert [r["id"] for r in member["items"]] == ["abs_2"] and member["next_offset"] == 2  # the private run is left out
        assert member["items"][0]["can_watch"] is False and member["items"][0]["can_stop"] is False
        assert "abs_other" not in json.dumps(member) + json.dumps(body) + json.dumps(last)
        assert client.get("/api/v1/action-browser/runs?limit=51", headers=OWNER).status_code == 422
        empty = client.get("/api/v1/action-browser/runs?offset=40", headers=OWNER).json()
        assert empty == {"items": [], "limit": 20, "offset": 40, "next_offset": None}
