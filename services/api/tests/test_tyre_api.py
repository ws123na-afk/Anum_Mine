"""Tyre planning end to end over the HTTP API (memory backend, fake Supabase REST).

Owner connects and maps, a member syncs and reports, a viewer only reads; reports are
exported, delivered through an approval, and scheduled through automation; the agent
tools answer tasks, chat and voice with the numbers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import automation, delivery, main
from anum_api.agent_tools import ToolCall, ToolDefinition, ToolResult
from anum_api.conversations import build_messages
from anum_api.dependencies import memory_repository
from anum_api.schemas import RiskLevel, TenantContext, WorkspaceMembership
from anum_api.tyre import api as tyre_api
from anum_api.tyre import service, sources, tools
from anum_api.tyre.store import memory_tyre_store
from tyre_fixtures import AS_OF, TABLES, FakeSupabase

TENANT, WORKSPACE = "tenant_tyre", "workspace_tyre"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_tyre", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_tyre", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_tyre", "x-user-roles": "viewer"}
OTHER = {**OWNER, "x-tenant-id": "tenant_other", "x-workspace-id": "workspace_other"}
NOW = datetime(AS_OF.year, AS_OF.month, AS_OF.day, 12, tzinfo=timezone.utc)
API = "/api/v1/tyre"

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def fake_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[FakeSupabase]:
    fake = FakeSupabase()
    monkeypatch.setattr(sources, "rest_client_factory", fake.client_factory())
    for module in (tyre_api, service, tools):
        monkeypatch.setattr(module, "utc_now", lambda: NOW)
    monkeypatch.setattr(automation, "engine", automation.LocalAutomationEngine(str(tmp_path / "automation.db")))
    memory_tyre_store.clear()
    yield fake
    memory_tyre_store.clear()


def connect_and_sync() -> dict[str, Any]:
    saved = client.put(f"{API}/connection", headers=OWNER, json={
        "kind": "supabase_rest", "label": "TyrePulse", "endpoint": "https://example.supabase.co", "secret": "example-key"})
    assert saved.status_code == 200, saved.text
    discovered = client.post(f"{API}/discover", headers=OWNER)
    assert discovered.status_code == 200, discovered.text
    mapped = client.put(f"{API}/mapping", headers=OWNER, json=discovered.json()["suggestion"])
    assert mapped.status_code == 200, mapped.text
    synced = client.post(f"{API}/sync", headers=MEMBER)
    assert synced.status_code == 200, synced.text
    return synced.json()


def test_setup_wizard_states_and_roles(fake_source: FakeSupabase) -> None:
    empty = client.get(f"{API}/setup", headers=VIEWER).json()
    assert empty["connection"] is None and empty["can_manage"] is False and empty["can_operate"] is False
    assert "tyres" in empty["fields"] and empty["presets"] == ["tyrepulse"]
    assert client.get(f"{API}/dashboard", headers=OWNER).json()["error"]["message"] == "Connect your tyre data first."

    denied = client.put(f"{API}/connection", headers=MEMBER, json={"kind": "supabase_rest", "endpoint": "https://example.supabase.co", "secret": "x" * 20})
    assert denied.status_code == 403 and "Only workspace owners" in denied.json()["error"]["message"]
    missing = client.put(f"{API}/connection", headers=OWNER, json={"kind": "supabase_rest", "endpoint": "https://example.supabase.co"})
    assert missing.status_code == 422 and "credential" in missing.json()["error"]["message"]
    bad_url = client.put(f"{API}/connection", headers=OWNER, json={"kind": "supabase_rest", "endpoint": "ftp://x", "secret": "example-key"})
    assert bad_url.status_code == 422

    saved = client.put(f"{API}/connection", headers=OWNER, json={
        "kind": "supabase_rest", "endpoint": "https://example.supabase.co/", "secret": "example-key-for-tests"})
    body = saved.json()
    assert body["connection"]["status"] == "untested" and body["connection"]["secret_hint"] == "…ests"
    assert "example-key-for-tests" not in saved.text
    stored = memory_tyre_store.get_connection(TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="x", roles=[]))
    assert stored and stored.secret_ciphertext and "example-key" not in stored.secret_ciphertext

    # Wrong key: the test says so and records the failure.
    failed = client.post(f"{API}/connection/test", headers=OWNER).json()
    assert failed["ok"] is False and "refused the key (HTTP 401)" in failed["message"]
    assert failed["setup"]["connection"]["last_error"]
    assert client.post(f"{API}/discover", headers=OWNER).status_code == 502
    # Keep the URL, change only the key.
    client.put(f"{API}/connection", headers=OWNER, json={"kind": "supabase_rest", "endpoint": "https://example.supabase.co", "secret": "example-key"})
    rekeyed = client.put(f"{API}/connection", headers=OWNER, json={"kind": "supabase_rest", "label": "Renamed", "endpoint": "https://example.supabase.co"})
    assert rekeyed.status_code == 200 and rekeyed.json()["connection"]["label"] == "Renamed"
    tested = client.post(f"{API}/connection/test", headers=OWNER).json()
    assert tested["ok"] is True and tested["setup"]["connection"]["status"] == "ok"

    discovery = client.post(f"{API}/discover", headers=OWNER).json()
    assert discovery["suggestion"]["preset"] == "tyrepulse" and len(discovery["tables"]) == len(TABLES)
    assert client.get(f"{API}/presets/tyrepulse", headers=VIEWER).json()["entities"]["tyres"]["source"] == "tyre_records"
    assert client.post(f"{API}/sync", headers=MEMBER).status_code == 409  # no mapping yet

    preview = client.post(f"{API}/mapping/preview", headers=OWNER, json=discovery["suggestion"]).json()
    assert preview["problems"] == [] and preview["stats"]["tyres"]["kept"] == 20 and preview["sample"]["tyres"]
    incomplete = client.put(f"{API}/mapping", headers=OWNER, json={"entities": {"vehicles": {"source": "vehicle_fleet", "fields": {"id": "asset_no"}}}})
    assert incomplete.json()["mapping_problems"]
    client.put(f"{API}/mapping", headers=OWNER, json=discovery["suggestion"])
    assert client.post(f"{API}/sync", headers=VIEWER).status_code == 403
    synced = client.post(f"{API}/sync", headers=MEMBER).json()
    assert synced["setup"]["sync"]["counts"]["tyres"] == 32 and synced["new_alerts"] == 6
    entities, counts = synced["setup"]["sync"]["entities"], synced["setup"]["sync"]["counts"]
    assert entities["inspections"]["issues"] == {"kept for the vehicle position (no tyre record matched)": 4}
    # Operations data: the work order older than the 90-day window was not read at all.
    assert counts["inspection_visits"] == 24 and counts["inspection_schedules"] == 3 and counts["checklists"] == 3
    assert counts["work_orders"] == 3 and entities["work_orders"]["read"] == 3
    assert counts["odometer_readings"] == 3 and counts["hour_readings"] == 1 and counts["sites"] == 1
    # Another tenant sees nothing.
    assert client.get(f"{API}/setup", headers=OTHER).json()["connection"] is None


def test_dashboard_forecasts_plan_and_csv() -> None:
    connect_and_sync()
    dashboard = client.get(f"{API}/dashboard", headers=VIEWER).json()
    assert dashboard["summary"]["tyres_fitted"] == 24 and dashboard["summary"]["below_minimum"] == 1
    assert dashboard["alert_counts"] == {"critical": 2, "warning": 4}
    assert dashboard["due_soon"][0]["serial"] == "fake-sn-0103" and dashboard["due_by_month"]["2026-10"] == 2

    page = client.get(f"{API}/forecasts", headers=VIEWER, params={"q": "fake-truck-01", "limit": 2}).json()
    assert page["total"] == 4 and len(page["items"]) == 2
    october = client.get(f"{API}/forecasts", headers=VIEWER, params={"month": "2026-10"}).json()
    assert {item["serial"] for item in october["items"]} == {"fake-sn-0103", "fake-sn-0101"}
    assert client.get(f"{API}/forecasts", headers=VIEWER, params={"month": "2026-13"}).status_code == 422
    low = client.get(f"{API}/forecasts", headers=VIEWER, params={"confidence": "low"}).json()
    assert all(item["confidence"] == "low" for item in low["items"])

    plan = client.get(f"{API}/purchase-plan", headers=VIEWER, params={"months": 12}).json()
    assert plan["total_tyres"] > 0 and plan["rows"][0]["month"] == "2026-10"
    csv_response = client.get(f"{API}/purchase-plan.csv", headers=VIEWER)
    assert csv_response.headers["content-type"].startswith("text/csv") and "attachment" in csv_response.headers["content-disposition"]
    assert "Plan by month, size and brand" in csv_response.text
    assert "fake-sn-0103" in client.get(f"{API}/forecasts.csv", headers=VIEWER).text


def test_alerts_acknowledge_reopen_and_resolve(fake_source: FakeSupabase) -> None:
    connect_and_sync()
    alerts = client.get(f"{API}/alerts", headers=VIEWER).json()
    assert alerts["total"] == 6 and alerts["items"][0]["severity"] == "critical"
    low = next(item for item in alerts["items"] if item["kind"] == "low_tread")
    assert client.post(f"{API}/alerts/{low['id']}/acknowledge", headers=VIEWER).status_code == 403
    acked = client.post(f"{API}/alerts/{low['id']}/acknowledge", headers=MEMBER).json()
    assert acked["status"] == "acknowledged" and acked["acknowledged_by"] == "member_tyre"
    assert client.get(f"{API}/alerts", headers=VIEWER, params={"status": "open"}).json()["total"] == 5
    assert client.post(f"{API}/alerts/{low['id']}/reopen", headers=MEMBER).json()["status"] == "open"
    searched = client.get(f"{API}/alerts", headers=VIEWER, params={"q": "fake-sn-0201"}).json()
    assert [item["kind"] for item in searched["items"]] == ["under_inflation"]
    assert client.post(f"{API}/alerts/nope/acknowledge", headers=MEMBER).status_code == 404

    # The worn tyre is replaced in the source: next sync resolves its alert.
    fake_source.rows["tyre_service_events"] = [r for r in fake_source.rows["tyre_service_events"] if r["tyre_serial"] != "fake-sn-0103" or r["cost"]]
    for row in fake_source.rows["tyre_records"]:
        if row["serial_no"] == "fake-sn-0103":
            row["status"], row["removal_date"], row["removal_reason"] = "Removed", AS_OF.isoformat(), "worn out"
    client.post(f"{API}/sync", headers=MEMBER)
    resolved = client.get(f"{API}/alerts", headers=VIEWER, params={"status": "resolved"}).json()
    assert [item["kind"] for item in resolved["items"]] == ["low_tread"]
    assert client.post(f"{API}/alerts/{low['id']}/acknowledge", headers=MEMBER).status_code == 409

    # A higher minimum re-evaluates alerts on the same data.
    setup = client.get(f"{API}/setup", headers=OWNER).json()
    assert client.put(f"{API}/assumptions", headers=MEMBER, json=setup["assumptions"]).status_code == 403
    client.put(f"{API}/assumptions", headers=OWNER, json={**setup["assumptions"], "min_tread_mm": 7.0})
    assert client.get(f"{API}/alerts", headers=VIEWER, params={"kind": "low_tread"}).json()["total"] >= 1
    assert client.put(f"{API}/assumptions", headers=OWNER, json={"min_tread_mm": 0}).status_code == 422


def test_reports_generate_export_delete() -> None:
    assert client.post(f"{API}/reports", headers=MEMBER, json={"kind": "fleet_health"}).status_code == 409
    connect_and_sync()
    assert client.post(f"{API}/reports", headers=VIEWER, json={"kind": "fleet_health"}).status_code == 403
    created = client.post(f"{API}/reports", headers=MEMBER, json={"kind": "fleet_health"})
    assert created.status_code == 201
    report = created.json()
    assert report["content"]["highlights"][1] == {"label": "Tyres fitted", "value": 24}
    for kind in ("scrap_reasons", "purchase_plan", "pressure_compliance"):
        assert client.post(f"{API}/reports", headers=MEMBER, json={"kind": kind}).status_code == 201
    bad = client.post(f"{API}/reports", headers=MEMBER, json={"kind": "scrap_reasons", "period_from": "2026-10-01", "period_to": "2026-01-01"})
    assert bad.status_code == 422
    listed = client.get(f"{API}/reports", headers=VIEWER, params={"limit": 2}).json()
    assert listed["total"] == 4 and len(listed["items"]) == 2 and listed["items"][0]["highlights"]
    assert client.get(f"{API}/reports", headers=VIEWER, params={"kind": "fleet_health"}).json()["total"] == 1
    pdf = client.get(f"{API}/reports/{report['id']}/export", headers=VIEWER, params={"format": "pdf"})
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    csv_body = client.get(f"{API}/reports/{report['id']}/export", headers=VIEWER, params={"format": "csv"}).text
    assert "Fleet tyre health" in csv_body and "Tyres fitted,24" in csv_body
    assert client.get(f"{API}/reports/{report['id']}", headers=OTHER).status_code == 404
    assert client.delete(f"{API}/reports/{report['id']}", headers=VIEWER).status_code == 403
    assert client.delete(f"{API}/reports/{report['id']}", headers=MEMBER).status_code == 204
    assert client.get(f"{API}/reports/{report['id']}", headers=VIEWER).status_code == 404


@pytest.fixture
def email_tool() -> Iterator[list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []

    async def send(call: ToolCall, context: TenantContext) -> ToolResult:
        attachment = delivery.resolve_attachment(call.arguments["attachments"][0], context)
        sent.append({**call.arguments, "bytes": attachment.content, "filename": attachment.filename})
        return ToolResult(status="succeeded", summary=f"Emailed {call.arguments['to']}.")

    registry = main.tool_registry
    registry.register(ToolDefinition(name="test.email_send", description="fake email", risk_level=RiskLevel.HIGH,
                                     delivery_channel="email"), send)
    yield sent
    registry._definitions.pop("test.email_send")
    registry._handlers.pop("test.email_send")


def test_delivery_needs_an_integration_then_an_approval(email_tool: list[dict[str, Any]]) -> None:
    connect_and_sync()
    report = client.post(f"{API}/reports", headers=MEMBER, json={"kind": "purchase_plan"}).json()
    main.tool_registry._definitions["test.email_send"].delivery_channel = None
    unavailable = client.post(f"{API}/reports/{report['id']}/deliver", headers=MEMBER, json={"channel": "email", "to": "fleet@example.com"})
    assert unavailable.status_code == 409 and "No email integration is connected" in unavailable.json()["error"]["message"]
    main.tool_registry._definitions["test.email_send"].delivery_channel = "email"
    assert client.get(f"{API}/delivery-channels", headers=VIEWER).json() == {"channels": ["email"]}
    assert client.post(f"{API}/reports/{report['id']}/deliver", headers=MEMBER, json={"channel": "email", "to": "not an address"}).status_code == 422

    requested = client.post(f"{API}/reports/{report['id']}/deliver", headers=MEMBER, json={"channel": "email", "to": "fleet@example.com"})
    body = requested.json()
    assert requested.status_code == 200 and body["status"] == "waiting_approval" and body["approval_id"]
    assert email_tool == []  # nothing leaves before approval
    assert body["report"]["deliveries"][0]["status"] == "waiting_approval"
    approval = next(a for a in client.get("/api/v1/approvals", headers=OWNER).json() if a["id"] == body["approval_id"])
    assert approval["action"] == "test.email_send" and approval["arguments"]["attachments"][0]["kind"] == "tyre_report"
    approved = client.post(f"/api/v1/approvals/{body['approval_id']}/approve", headers=OWNER, json={"payload_hash": body["payload_hash"]})
    assert approved.status_code == 200, approved.text
    assert len(email_tool) == 1 and email_tool[0]["to"] == "fleet@example.com"
    assert email_tool[0]["bytes"].startswith(b"%PDF") and email_tool[0]["filename"].endswith(".pdf")
    assert "Tyres to buy" in email_tool[0]["text"]


def test_report_schedules_run_through_automation(email_tool: list[dict[str, Any]]) -> None:
    connect_and_sync()
    memory_repository.save_membership(WorkspaceMembership(
        tenant_id=TENANT, workspace_id=WORKSPACE, user_id="member_tyre", role="member", created_at=NOW, updated_at=NOW))
    assert client.post(f"{API}/report-schedules", headers=VIEWER, json={"kind": "fleet_health", "cadence": "daily"}).status_code == 403
    bad = client.post(f"{API}/report-schedules", headers=MEMBER, json={"kind": "fleet_health", "cadence": "weekly", "timezone": "Mars/Base"})
    assert bad.status_code == 422
    created = client.post(f"{API}/report-schedules", headers=MEMBER, json={
        "kind": "fleet_health", "cadence": "weekly", "weekday": 0, "time": "06:30", "timezone": "Asia/Riyadh",
        "delivery": {"channel": "email", "to": "fleet@example.com", "format": "csv"}})
    assert created.status_code == 201, created.text
    schedule = created.json()
    assert schedule["cron"] == "30 6 * * 0" and schedule["description"] == "Every Sunday at 06:30" and schedule["next_run_at"]
    assert [s["id"] for s in client.get(f"{API}/report-schedules", headers=VIEWER).json()] == [schedule["id"]]

    run = client.post(f"/api/v1/automation/workflows/{schedule['workflow_id']}/runs", headers=MEMBER)
    assert run.status_code == 201, run.text
    reports = client.get(f"{API}/reports", headers=VIEWER).json()["items"]
    assert len(reports) == 1 and reports[0]["source"] == "schedule" and reports[0]["schedule_id"] is None
    assert reports[0]["deliveries"][0]["status"] == "waiting_approval"
    # Replaying the same run does not produce a second report.
    automation.dispatch_step_actions([automation.current_engine().get_run(
        TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="member_tyre", roles=["member"]), run.json()["id"])])
    assert client.get(f"{API}/reports", headers=VIEWER).json()["total"] == 1

    assert client.delete(f"{API}/report-schedules/{schedule['id']}", headers=MEMBER).status_code == 204
    assert client.get(f"{API}/report-schedules", headers=VIEWER).json() == []
    assert client.delete(f"{API}/report-schedules/{schedule['id']}", headers=MEMBER).status_code == 404


def test_disconnect_forgets_data_but_keeps_reports() -> None:
    connect_and_sync()
    client.post(f"{API}/reports", headers=MEMBER, json={"kind": "wear_rates"})
    assert client.delete(f"{API}/connection", headers=MEMBER).status_code == 403
    assert client.delete(f"{API}/connection", headers=OWNER).status_code == 204
    assert client.get(f"{API}/setup", headers=OWNER).json()["connection"] is None
    assert client.get(f"{API}/alerts", headers=OWNER).json()["total"] == 0
    assert client.get(f"{API}/reports", headers=OWNER).json()["total"] == 1
    assert client.delete(f"{API}/connection", headers=OWNER).status_code == 404


def test_agent_tools_answer_tasks_chat_and_voice() -> None:
    owner = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_tyre", roles=["owner"])
    not_connected = asyncio.run(main.tool_registry.execute(ToolCall(name="tyre_fleet_summary"), owner))
    assert not_connected.status == "failed" and "not connected" in not_connected.summary
    connect_and_sync()
    for name in tools.TOOL_NAMES:
        assert main.tool_registry.definition(name).risk_level == RiskLevel.LOW

    summary = asyncio.run(main.tool_registry.execute(ToolCall(name="tyre_fleet_summary"), owner))
    assert "24 tyres fitted" in summary.summary and "1 at or below the 3 mm minimum" in summary.summary
    due = asyncio.run(main.tool_registry.execute(ToolCall(name="tyres_due", arguments={"month": "2026-10"}), owner))
    assert due.summary.startswith("2 tyres are projected for removal in 2026-10") and "fake-sn-0103" in due.summary
    plan = asyncio.run(main.tool_registry.execute(ToolCall(name="purchase_plan", arguments={"months": 3}), owner))
    assert "Purchase plan for the next 3 months" in plan.summary and "SAR" in plan.summary
    alerts = asyncio.run(main.tool_registry.execute(ToolCall(name="tyre_alerts"), owner))
    assert alerts.summary.startswith("6 open tyre alerts (2 critical")
    outsider = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="x", roles=["guest"])
    assert asyncio.run(main.tool_registry.execute(ToolCall(name="tyre_alerts"), outsider)).status == "failed"

    # A task: the skill and planner pick the tool from the prompt; low risk runs without approval.
    task = client.post("/api/v1/tasks", headers=OWNER, json={"title": "Tyres", "prompt": "Which tyres are due in October 2026?"}).json()
    ran = client.post(f"/api/v1/tasks/{task['id']}/run", headers=OWNER).json()
    assert ran["task"]["status"] == "completed" and ran["approval"] is None
    assert "2 tyres are projected for removal in 2026-10" in ran["run"]["result"]

    # Chat: the tool output reaches the model as a labeled data block, under policy and audit.
    grounding = asyncio.run(tools.tyre_grounding(owner, "how many tyre alerts are open?"))
    assert grounding is not None and grounding.call.name == "tyre_alerts"
    messages = build_messages([], "how many tyre alerts are open?", data_blocks=[grounding.block])
    assert "6 open tyre alerts" in messages[0]["content"] and "untrusted" in messages[0]["content"].lower()
    assert asyncio.run(tools.tyre_grounding(owner, "what is the weather")) is None
    audit = memory_repository.list_audit_records(owner)
    assert any(record.action == "tool.call" and record.target == "tyre_alerts" for record in audit)
    assert any(record.action == "tyre.sync" for record in audit)
    # The sync announced its new alerts once (counts only) for phone push.
    raised = [event for event in memory_repository.list_events(owner) if event.type == "tyre_alerts.raised"]
    assert raised and raised[0].payload == {"critical": 2, "warning": 4}

    # Voice (mock provider): the spoken reply carries the tool's numbers.
    session_id = client.post("/api/v1/voice/sessions", headers=OWNER, json={}).json()["id"]
    segment = client.post(f"/api/v1/voice/sessions/{session_id}/transcript", headers=OWNER,
                          json={"text": "Give me a tyre fleet summary", "is_final": True, "client_sequence": 0}).json()
    spoken = client.post(f"/api/v1/voice/sessions/{session_id}/ask", headers=OWNER, json={"transcript_segment_id": segment["id"]}).json()
    assert "24 tyres fitted" in spoken["reply"]


def test_new_tyre_reports_reach_the_shared_reports() -> None:
    """A generated tyre report is copied to the shared reports (docs/reports.md) with its
    numbers as metrics and tiles and its first table as the data snapshot."""
    from anum_api.reports import report_store

    report_store.clear()
    connect_and_sync()
    created = client.post(f"{API}/reports", headers=MEMBER, json={"kind": "fleet_health"})
    assert created.status_code == 201
    shared = client.get("/api/v1/reports", headers=VIEWER, params={"type": "tyre"}).json()["items"]
    assert [item["title"] for item in shared] == ["Fleet tyre health"]
    assert shared[0]["created_by_kind"] == "user" and shared[0]["section_count"] == 1
    full = client.get(f"/api/v1/reports/{shared[0]['id']}", headers=VIEWER).json()
    assert {"label": "Tyres fitted", "value": "24", "unit": None, "change_percent": None} in full["metrics"]
    tiles = full["sections"][0]["items"]
    assert {"label": "Tyres fitted", "value": 24.0} == {k: tiles[1][k] for k in ("label", "value")}
    assert full["data"]["columns"][0] == "Tyre"
    # A failure in the shared module never fails the tyre report.
    original = service.shared_report_payload
    service.shared_report_payload = lambda report: (_ for _ in ()).throw(ValueError("broken"))  # type: ignore[assignment]
    try:
        assert client.post(f"{API}/reports", headers=MEMBER, json={"kind": "scrap_reasons"}).status_code == 201
    finally:
        service.shared_report_payload = original  # type: ignore[assignment]
    report_store.clear()


def test_tyre_questions_in_chat_answer_with_tyre_cards() -> None:
    import json

    from anum_api.agent_skills import default_skill_registry
    from anum_api.chat_tools import chat_intent

    skills, names = default_skill_registry(), main.tool_registry.names
    assert chat_intent("Which tyres are due in October 2026?", skills, names) == "tyre"
    assert chat_intent("كم عدد تنبيهات الإطارات؟", skills, names) == "tyre"

    def chat(question: str) -> dict:
        conversation = client.post("/api/v1/conversations", headers=OWNER, json={}).json()["id"]
        response = client.post(f"/api/v1/conversations/{conversation}/ask", headers=OWNER, json={"content": question})
        assert response.status_code == 200, response.text
        frames = [dict(line.split(": ", 1) for line in frame.splitlines()) for frame in response.text.strip().split("\n\n")]
        return [json.loads(f["data"])["card"] for f in frames if f["event"] == "tool"][-1]

    # Not connected yet: the task fails and the card says how to fix it.
    failed = chat("how many tyre alerts are open?")
    assert failed["state"] == "failed" and failed["category"] == "tyre" and "not connected" in failed["summary"]

    connect_and_sync()
    due = chat("Which tyres are due in October 2026?")
    assert due["state"] == "results" and due["tool"] == "tyres_due" and due["total"] == 2
    assert {item["title"] for item in due["items"]} >= {"fake-sn-0103"} and all(item["kind"] == "tyre" for item in due["items"])
    alerts = chat("how many tyre alerts are open?")
    assert alerts["tool"] == "tyre_alerts" and alerts["total"] == 6
    assert sum("priority_high" in item["badges"] for item in alerts["items"]) == 2



def test_voice_lookups_share_one_cached_load_and_a_sync_or_delete_refreshes_it() -> None:
    from anum_api.tyre import read_cache

    owner = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_tyre", roles=["owner"])
    connect_and_sync()

    def run(name: str, **arguments: Any) -> Any:
        return asyncio.run(main.tool_registry.execute(ToolCall(name=name, arguments=arguments), owner))

    read_cache.clear()
    before = read_cache.stats()["builds"]
    first = run("tyre_fleet_summary")
    run("tyres_due", month="2026-10")
    run("purchase_plan", months=3)
    again = run("tyre_fleet_summary")
    assert read_cache.stats()["builds"] - before == 1  # one load and forecast for four lookups
    assert again.summary == first.summary
    # A sync (new snapshot) drops the entry: the next lookup loads again.
    assert client.post(f"{API}/sync", headers=MEMBER).status_code == 200
    run("tyre_fleet_summary")
    assert read_cache.stats()["builds"] - before == 2
    # So does forgetting the connection: nothing stale is answered.
    assert client.delete(f"{API}/connection", headers=OWNER).status_code == 204
    assert "not connected" in run("tyre_fleet_summary").summary


def test_a_slow_tyre_lookup_for_voice_times_out_without_blocking_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    connect_and_sync()
    owner = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_tyre", roles=["owner"])

    def slow(context: TenantContext) -> Any:
        time.sleep(1.0)
        raise AssertionError("not reached in time")

    monkeypatch.setattr(service, "forecasts_cached", slow)

    async def scenario() -> tuple[Any, float]:
        ticks = 0

        async def heartbeat() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        grounding = await tools.tyre_grounding(owner, "how many tyres are due?", timeout=0.2)
        beat.cancel()
        return grounding, ticks

    grounding, ticks = asyncio.run(scenario())
    assert grounding is not None and grounding.result.status == "failed"
    assert "timed out" in grounding.result.summary and grounding.result.audit_metadata["outcome"] == "timeout"
    assert ticks >= 5  # the event loop kept running while the lookup was computing
