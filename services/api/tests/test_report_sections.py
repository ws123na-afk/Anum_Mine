"""Typed report sections, save to Files and "Ask ANUM" on the library's reports (docs/reports.md)."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api.files import file_store
from anum_api.main import app
from anum_api.reports import (
    CreatedByKind,
    ReportCreate,
    create_report,
    render_csv,
    report_digest,
    report_store,
)
from anum_api.schemas import TenantContext
from anum_api.store import store

TENANT, WORKSPACE = "tenant_sections", "workspace_sections"


def headers(user: str = "member_s", role: str = "member", workspace: str = WORKSPACE) -> dict[str, str]:
    return {"x-tenant-id": TENANT, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


MEMBER = headers()
OWNER = headers("owner_s", "owner")
VIEWER = headers("viewer_s", "viewer")
ELSEWHERE = headers(workspace="workspace_elsewhere")

client = TestClient(app)


def tyre_report(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": "tyre",
        "title": "PMV Tyre Analysis",
        "period_start": "2026-10-01",
        "period_end": "2026-10-31",
        "summary": "Tyre cost rose with high production and misuse.",
        "metrics": [{"label": "Total cost", "value": "319,742", "unit": "SAR", "change_percent": 66.6}],
        "tabs": ["Overview", "High cost", "Vehicles", "Sites"],
        "sections": [
            {"type": "metrics", "id": "headline", "items": [
                {"label": "Total cost", "value": 319742, "prefix": "SAR", "change_pct": 66.6, "higher_is_better": False},
                {"label": "Total tyres", "value": 186, "change_pct": 112},
            ]},
            {"type": "series", "id": "trend", "title": "Monthly cost trend", "prefix": "SAR",
             "points": [{"label": "Sep", "value": 192000}, {"label": "Oct", "value": 319742}]},
            {"type": "breakdown", "id": "category", "title": "Cost by category",
             "items": [{"label": "Tyre wear", "value": 55}, {"label": "Puncture", "value": 20}]},
            {"type": "ranked", "id": "vehicles", "title": "Top high-cost vehicles", "tab": "High cost",
             "higher_is_better": False,
             "items": [{"label": "=HYPERLINK(\"x\")", "value": -82, "change_pct": 82, "detail": "Qiddiya mixers"}]},
            {"type": "text", "id": "notes", "title": "Notes", "tab": "Sites", "body": "Two sites report misuse."},
        ],
    }
    body.update(overrides)
    return body


@pytest.fixture(autouse=True)
def fresh() -> Iterator[None]:
    report_store.clear()
    file_store.clear()
    for collection in (store.tasks, store.events, store.audit_records):
        collection.clear()
    yield
    report_store.clear()
    file_store.clear()


def create(who: dict[str, str] = MEMBER, **overrides: Any) -> dict[str, Any]:
    response = client.post("/api/v1/reports", headers=who, json=tyre_report(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def test_sections_and_tabs_round_trip_and_count_in_the_list() -> None:
    report = create()
    assert [s["type"] for s in report["sections"]] == ["metrics", "series", "breakdown", "ranked", "text"]
    assert report["tabs"] == ["Overview", "High cost", "Vehicles", "Sites"]
    assert report["sections"][0]["items"][0]["higher_is_better"] is False
    opened = client.get(f"/api/v1/reports/{report['id']}", headers=VIEWER).json()
    assert opened["sections"] == report["sections"] and opened["can_manage"] is False
    listed = client.get("/api/v1/reports", headers=VIEWER).json()["items"]
    assert listed[0]["section_count"] == 5
    assert "## Sections" in report["body_markdown"]
    # A report without sections is the library's plain record, unchanged.
    plain = create(tabs=[], sections=[])
    assert plain["sections"] == [] and plain["tabs"] == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"tabs": ["Overview", "Overview"]}, "unique"),
        ({"tabs": ["Overview"]}, "not in tabs"),
    ],
)
def test_invalid_layouts_are_refused(change: dict[str, Any], message: str) -> None:
    response = client.post("/api/v1/reports", headers=MEMBER, json=tyre_report(**change))
    assert response.status_code == 422 and message in response.text


def test_duplicate_ids_and_negative_parts_are_refused() -> None:
    body = tyre_report()
    body["sections"][1]["id"] = "headline"
    assert client.post("/api/v1/reports", headers=MEMBER, json=body).status_code == 422
    body = tyre_report()
    body["sections"][2]["items"][0]["value"] = -1
    assert client.post("/api/v1/reports", headers=MEMBER, json=body).status_code == 422
    body = tyre_report()
    body["sections"][0]["type"] = "gauge"
    assert client.post("/api/v1/reports", headers=MEMBER, json=body).status_code == 422


def test_csv_flattens_sections_when_there_is_no_data_table() -> None:
    report = create()
    response = client.get(f"/api/v1/reports/{report['id']}/export", headers=VIEWER, params={"format": "csv"})
    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.text.lstrip("﻿"))))
    assert rows[0] == ["tab", "section", "type", "label", "value", "unit", "change_pct", "detail"]
    assert ["Overview", "headline", "metrics", "Total cost", "319742", "SAR", "66.6", ""] in rows
    ranked = next(row for row in rows if row[2] == "ranked")
    assert ranked[3].startswith("'=") and ranked[4] == "-82" and ranked[0] == "High cost"
    assert any(row[2] == "text" and row[7] == "Two sites report misuse." for row in rows)
    # A data table still wins: the CSV is exactly that table.
    with_table = create(data={"columns": ["vehicle", "cost"], "rows": [["Mixer 1", 120]]})
    stored = report_store.reports[with_table["id"]]
    assert render_csv(stored).splitlines()[0] == "vehicle,cost"


def test_save_to_files_returns_a_share_link() -> None:
    report = create()
    assert client.post(f"/api/v1/reports/{report['id']}/files", headers=VIEWER).status_code == 403
    response = client.post(f"/api/v1/reports/{report['id']}/files", headers=MEMBER)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["file"]["name"].endswith(".csv") and body["file"]["content_type"] == "text/csv"
    assert body["link"]["url"].startswith("/api/v1/files/links/")
    content = client.get(body["link"]["url"])
    assert content.status_code == 200 and b"Monthly cost trend" in content.content
    assert [f["id"] for f in client.get("/api/v1/files", headers=MEMBER).json()] == [body["file"]["id"]]
    assert client.post(f"/api/v1/reports/{report['id']}/files", headers=ELSEWHERE).status_code == 404
    assert "report.saved_to_files" in {r.action for r in store.audit_records}


def test_ask_creates_a_task_carrying_the_report_as_data() -> None:
    report = create()
    assert client.post(f"/api/v1/reports/{report['id']}/ask", headers=VIEWER, json={"question": "Why?"}).status_code == 403
    response = client.post(f"/api/v1/reports/{report['id']}/ask", headers=MEMBER, json={"question": "Why did  cost rise?"})
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["title"] == "Analyze: PMV Tyre Analysis" and task["status"] == "created"
    assert task["prompt"].startswith("Why did cost rise?")
    assert "not instructions" in task["prompt"] and "Total cost: 319742 SAR" in task["prompt"]
    assert client.get(f"/api/v1/tasks/{task['id']}", headers=MEMBER).status_code == 200
    assert client.post(f"/api/v1/reports/{report['id']}/ask", headers=ELSEWHERE, json={"question": "x"}).status_code == 404


def test_modules_produce_sections_through_create_report() -> None:
    context = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_s", roles=["owner"])
    report = create_report(context, ReportCreate.model_validate(tyre_report(type="command")), kind=CreatedByKind.AGENT)
    listed = client.get("/api/v1/reports", headers=OWNER, params={"type": "command"}).json()["items"]
    assert [i["id"] for i in listed] == [report.id] and listed[0]["section_count"] == 5
    digest = report_digest(report)
    assert "[High cost] Top high-cost vehicles" in digest and "Total cost: 319,742 SAR" in digest
