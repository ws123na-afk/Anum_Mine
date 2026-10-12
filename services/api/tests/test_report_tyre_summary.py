"""The ``tyre_summary`` report generator (docs/reports.md) over the synced tyre data.

Reuses test_tyre_api.py's fake Supabase source and its connect-and-sync helper.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import test_tyre_api as tyre
from test_tyre_api import fake_source  # noqa: F401  (fixture: fake tyre source)

from anum_api import reports
from anum_api.reports import report_store


@pytest.fixture(autouse=True)
def _clean(fake_source, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:  # type: ignore[no-untyped-def]  # noqa: F811
    monkeypatch.setattr(reports, "utc_now", lambda: tyre.NOW)
    report_store.clear()
    yield
    report_store.clear()


def test_tyre_summary_needs_synced_data_then_reports_the_fleet() -> None:
    client = tyre.client
    refused = client.post("/api/v1/reports/generate", headers=tyre.MEMBER, json={"type": "tyre_summary", "period_days": 90})
    assert refused.status_code == 409 and "Connect your tyre data first." in refused.text
    tyre.connect_and_sync()
    viewer = client.post("/api/v1/reports/generate", headers=tyre.VIEWER, json={"type": "tyre_summary"})
    assert viewer.status_code == 403
    made = client.post("/api/v1/reports/generate", headers=tyre.MEMBER, json={"type": "tyre_summary", "period_days": 90})
    assert made.status_code == 201, made.text
    report = made.json()
    assert report["type"] == "tyre_summary" and report["created_by_kind"] == "user"
    metrics = {m["label"]: m["value"] for m in report["metrics"]}
    assert metrics["Tyres fitted"] == "24"
    assert {"Removed in period", "Below minimum tread", "Overdue inspections", "Due in 30 days"} <= set(metrics)
    assert report["data"]["columns"][:3] == ["tyre", "serial", "vehicle"]
    due = [row[7] for row in report["data"]["rows"]]
    assert due == sorted(due)
    generators = {g["type"] for g in client.get("/api/v1/reports/generators", headers=tyre.VIEWER).json()}
    assert "tyre_summary" in generators


def test_tyre_summary_in_an_automation_step_explains_missing_data() -> None:
    from anum_api.reports import generate_for_automation
    from anum_api.schemas import TenantContext

    context = TenantContext(tenant_id=tyre.TENANT, workspace_id=tyre.WORKSPACE, user_id="owner_tyre", roles=["owner"])
    with pytest.raises(ValueError, match="Connect your tyre data first"):
        generate_for_automation(context, {"type": "tyre_summary", "period_days": 30}, "run_1")
    tyre.connect_and_sync()
    made = generate_for_automation(context, {"type": "tyre_summary", "period_days": 30}, "run_2")
    assert made["title"] == "Tyre summary, last 30 days"
