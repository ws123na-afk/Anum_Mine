"""System health (docs/system-health.md) against the in-memory backend: the platform operator's read-only
report, each check's thresholds, the daily notice to owners when something is red and the notice of an
unexpected restart."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anum_api import system_health as sh
from anum_api.dependencies import memory_repository
from anum_api.main import app, store
from anum_api.settings import settings
from anum_api.team_work import memory_team_store

client = TestClient(app)
OPERATOR = {"x-tenant-id": "tenant_a", "x-workspace-id": "workspace_a", "x-user-id": "user_owner", "x-user-roles": "owner,platform_operator"}
OWNER = {**OPERATOR, "x-user-roles": "owner"}
NOW = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):  # type: ignore[no-untyped-def]
    store.audit_records.clear()
    store.memberships.clear()
    memory_team_store.clear()
    sh.error_counter.reset()
    sh.clear_beats()
    monkeypatch.setattr(settings, "backup_status_dir", str(tmp_path))
    monkeypatch.setattr(settings, "model_provider", "mock")
    monkeypatch.setattr(settings, "smtp_host", None)
    monkeypatch.setattr(settings, "smtp_from", None)
    monkeypatch.setattr(settings, "automation_scheduler_enabled", False)
    monkeypatch.setattr(settings, "system_health_tenant_id", "")
    monkeypatch.setattr(settings, "system_health_workspace_id", "")
    yield tmp_path
    store.audit_records.clear()
    store.memberships.clear()
    memory_team_store.clear()


def _status(folder: Path, kind: str, ok: bool, finished: datetime, success: datetime | None) -> None:
    (folder / f"last-{kind}.json").write_text(json.dumps({
        "ok": ok, "finished_at": finished.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_success_at": success.strftime("%Y-%m-%dT%H:%M:%SZ") if success else None, "keep_days": 7}))


def _by_key(report: dict) -> dict[str, dict]:
    return {item["key"]: item for item in report["items"]}


def _owners(*user_ids: str) -> None:
    from anum_api.schemas import WorkspaceMembership, utc_now

    for user_id in user_ids:
        memory_repository.store.memberships[("tenant_a", "workspace_a", user_id)] = WorkspaceMembership(
            tenant_id="tenant_a", workspace_id="workspace_a", user_id=user_id, role="owner", active=True,
            created_at=utc_now(), updated_at=utc_now())


def test_only_a_platform_operator_reads_it_and_it_shows_no_secret(_clean: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    denied = client.get("/api/v1/platform/health", headers=OWNER)
    assert denied.status_code == 403
    monkeypatch.setattr(settings, "model_provider", "anthropic")
    monkeypatch.setattr(settings, "model_api_key", "sk-ant-example")
    ok = client.get("/api/v1/platform/health", headers=OPERATOR)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert [item["key"] for item in body["items"]] == [
        "backup_local", "backup_offsite", "restore_drill", "ai_model", "disk", "memory", "email", "scheduler", "errors",
        "restarts", "notices"]
    assert body["you"] == {"tenant_id": "tenant_a", "workspace_id": "workspace_a"}
    assert _by_key(body)["ai_model"] == {"key": "ai_model", "state": "green",
                                         "detail": {"provider": "anthropic", "placeholder": False, "key_missing": False}}
    text = ok.text
    assert "sk-ant-example" not in text and str(_clean) not in text


def test_backups_are_red_after_26_hours_and_amber_when_the_last_run_failed(_clean: Path) -> None:
    _status(_clean, "local", True, NOW - timedelta(hours=3), NOW - timedelta(hours=3))
    _status(_clean, "offsite", False, NOW - timedelta(hours=3), NOW - timedelta(hours=20))
    _status(_clean, "drill", True, NOW - timedelta(days=10), NOW - timedelta(days=10))
    items = _by_key(sh.evaluate(NOW))
    assert items["backup_local"]["state"] == "green" and items["backup_local"]["detail"]["age_hours"] == 3.0
    assert items["backup_offsite"]["state"] == "amber" and items["backup_offsite"]["detail"]["last_ok"] is False
    assert items["restore_drill"]["state"] == "green"
    _status(_clean, "local", True, NOW - timedelta(hours=27), NOW - timedelta(hours=27))
    _status(_clean, "offsite", False, NOW - timedelta(hours=1), None)
    _status(_clean, "drill", False, NOW - timedelta(days=1), NOW - timedelta(days=40))
    items = _by_key(sh.evaluate(NOW))
    assert items["backup_local"]["state"] == "red"
    assert items["backup_offsite"]["state"] == "red" and items["backup_offsite"]["detail"]["ever"] is False
    assert items["restore_drill"]["state"] == "red"
    (_clean / "last-drill.json").unlink()
    assert _by_key(sh.evaluate(NOW))["restore_drill"]["state"] == "amber"  # never run


def test_unrecorded_backups_and_broken_status_files_are_not_guessed(_clean: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (_clean / "last-local.json").write_text("not json")
    assert _by_key(sh.evaluate(NOW))["backup_local"]["state"] == "red"
    monkeypatch.setattr(settings, "backup_status_dir", "")
    items = _by_key(sh.evaluate(NOW))
    assert items["backup_local"]["state"] == "unknown" and items["backup_local"]["detail"]["recorded"] is False
    assert items["restore_drill"]["state"] == "unknown"


def test_model_email_scheduler_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    items = _by_key(sh.evaluate(NOW))
    assert items["ai_model"]["state"] == "red" and items["ai_model"]["detail"]["placeholder"] is True
    assert items["email"]["state"] == "amber" and items["scheduler"] == {"key": "scheduler", "state": "amber", "detail": {"enabled": False}}
    assert items["errors"]["state"] == "green" and items["errors"]["detail"]["count_24h"] == 0
    monkeypatch.setattr(settings, "model_provider", "openai-compatible")
    monkeypatch.setattr(settings, "model_api_key", None)
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "ANUM <no-reply@example.com>")
    monkeypatch.setattr(settings, "automation_scheduler_enabled", True)
    monkeypatch.setattr(sh, "STARTED_AT", NOW - timedelta(hours=1))
    items = _by_key(sh.evaluate(NOW))
    assert items["ai_model"]["state"] == "red" and items["ai_model"]["detail"]["key_missing"] is True
    assert items["email"]["state"] == "green"
    assert items["scheduler"]["state"] == "red"  # on, but no pass in an hour
    sh.heartbeat("team_reminders", NOW - timedelta(minutes=2))
    assert _by_key(sh.evaluate(NOW))["scheduler"]["state"] == "green"
    sh.install_error_counter()
    for _ in range(3):
        logging.getLogger("anum.test").error("boom")
    errors = _by_key(sh.evaluate())["errors"]
    assert errors["state"] == "amber" and errors["detail"]["count_24h"] == 3
    assert "boom" not in json.dumps(errors)


def test_disk_and_memory_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(sh.shutil, "disk_usage", lambda path: usage(100, 95, 5))
    assert _by_key(sh.evaluate(NOW))["disk"]["state"] == "red"
    monkeypatch.setattr(sh.shutil, "disk_usage", lambda path: usage(100, 85, 15))
    assert _by_key(sh.evaluate(NOW))["disk"]["state"] == "amber"
    monkeypatch.setattr(sh, "_meminfo", lambda: (30, 100))
    monkeypatch.setattr(sh, "_read_int", lambda path: {"/sys/fs/cgroup/memory.max": 1000, "/sys/fs/cgroup/memory.current": 950}[path])
    memory = _by_key(sh.evaluate(NOW))["memory"]
    assert memory["state"] == "red" and memory["detail"]["api_free_pct"] == 5.0 and memory["detail"]["available_pct"] == 30.0
    monkeypatch.setattr(sh, "_read_int", lambda path: None)
    assert _by_key(sh.evaluate(NOW))["memory"]["state"] == "green"
    monkeypatch.setattr(sh, "_meminfo", lambda: None)
    assert _by_key(sh.evaluate(NOW))["memory"]["state"] == "unknown"


def test_one_notice_a_day_to_every_active_owner_when_something_is_red(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api.schemas import TenantContext

    _owners("user_owner", "user_second")
    # Not configured: no notice, and the page says how to turn them on.
    assert sh.run_health_check(NOW) == {"overall": "red", "notified": 0}
    assert _by_key(sh.evaluate(NOW))["notices"]["state"] == "amber"
    monkeypatch.setattr(settings, "system_health_tenant_id", "tenant_a")
    monkeypatch.setattr(settings, "system_health_workspace_id", "workspace_a")
    assert sh.run_health_check(NOW) == {"overall": "red", "notified": 2}
    assert sh.run_health_check(NOW + timedelta(hours=1))["notified"] == 0  # once a UTC day
    for user_id in ("user_owner", "user_second"):
        ctx = TenantContext(tenant_id="tenant_a", workspace_id="workspace_a", user_id=user_id, roles=["owner"])
        notices = memory_team_store.list_notifications(ctx, limit=50, before=None, unread_only=False, kind=sh.NOTICE_KIND)
        assert len(notices) == 1
        assert notices[0].title.startswith("System health:") and "Off-site backup" in notices[0].body
        assert "—" not in notices[0].body and "–" not in notices[0].body
    assert sh.run_health_check(NOW + timedelta(days=1))["notified"] == 2  # the next day again


def test_a_start_without_a_clean_stop_before_it_notices_the_owners(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api.schemas import TenantContext

    _owners("user_owner")
    assert sh.record_start(NOW) is False  # not configured: nothing recorded
    monkeypatch.setattr(settings, "system_health_tenant_id", "tenant_a")
    monkeypatch.setattr(settings, "system_health_workspace_id", "workspace_a")
    assert sh.record_start(NOW) is False  # the first start ever
    sh.record_stop(NOW + timedelta(minutes=30))
    assert sh.record_start(NOW + timedelta(hours=1)) is False  # a clean stop came first
    assert sh.record_start(NOW + timedelta(hours=2)) is True  # no stop: the API died
    ctx = TenantContext(tenant_id="tenant_a", workspace_id="workspace_a", user_id="user_owner", roles=["owner"])
    notices = memory_team_store.list_notifications(ctx, limit=50, before=None, unread_only=False, kind=sh.RESTART_KIND)
    assert len(notices) == 1 and "without shutting down" in notices[0].body
    restarts = _by_key(sh.evaluate(NOW + timedelta(hours=3)))["restarts"]
    assert restarts["state"] == "red" and restarts["detail"]["last_unexpected_at"].startswith("2026-11-12T12:00")
    assert _by_key(sh.evaluate(NOW + timedelta(days=3)))["restarts"]["state"] == "amber"
    assert _by_key(sh.evaluate(NOW + timedelta(days=8)))["restarts"]["state"] == "green"


def test_the_monitor_only_runs_with_a_notice_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    assert sh.build_system_health_monitor(settings) is None
    monkeypatch.setattr(settings, "system_health_tenant_id", "tenant_a")
    monkeypatch.setattr(settings, "system_health_workspace_id", "workspace_a")
    monitor = sh.build_system_health_monitor(settings)
    assert monitor is not None and monitor.interval == settings.system_health_check_seconds
