"""Record retention (owner decision O5) without a database: routes, refusals, standard periods and the purge loop.

The PostgreSQL behaviour (discovery as ``anum_maintenance``, purge as the app role, audit) is in
``test_postgres_record_retention.py``.
"""
from __future__ import annotations

import os
import signal
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from anum_api import record_retention as rr
from anum_api.main import app
from anum_api.settings import settings

HEADERS = {"x-tenant-id": "tenant_a", "x-workspace-id": "workspace_a", "x-user-id": "owner_a", "x-user-roles": "owner"}
URL = "/api/v1/workspace/record-retention"


def body(**over):  # type: ignore[no-untyped-def]
    return {"transcript_days": 60, "visitor_days": 400, "expected_revision": 0, "input_mode": "click", **over}


def test_routes_are_registered() -> None:
    assert set(app.openapi()["paths"][URL]) == {"get", "put"}


def test_standard_periods_and_range_match_the_migration() -> None:
    source = open(os.path.join(os.path.dirname(__file__), "..", "migrations", "versions", "0120_record_retention.py"),
                  encoding="utf-8").read()
    assert f"MIN_TRANSCRIPT_DAYS = {rr.TRANSCRIPT_MIN_DAYS}" in source
    assert f"MIN_VISITOR_DAYS = {rr.VISITOR_MIN_DAYS}" in source and f"MAX_DAYS = {rr.MAX_DAYS}" in source
    assert (rr.TRANSCRIPT_DEFAULT_DAYS, rr.VISITOR_DEFAULT_DAYS) == (90, 365)
    from anum_api import record_retention_api as api

    assert api.BAD_TRANSCRIPT == f"Meeting transcripts can be kept for {rr.TRANSCRIPT_MIN_DAYS} to {rr.MAX_DAYS} days"
    assert api.BAD_VISITOR == f"Visitor records can be kept for {rr.VISITOR_MIN_DAYS} to {rr.MAX_DAYS} days"
    assert rr.RetentionSetting().transcripts == 90 and rr.RetentionSetting().visitors == 365
    assert rr.RetentionSetting(30, None).transcripts == 30 and rr.RetentionSetting(30, None).visitors == 365


def test_without_postgres_the_setting_answers_503_never_a_made_up_period(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "repository_backend", "memory")
    client = TestClient(app)
    assert client.get(URL, headers=HEADERS).status_code == 503
    assert client.put(URL, headers=HEADERS, json=body()).status_code == 503
    # Every refusal comes before the backend is touched: who, how and what are checked first.


def test_only_an_owner_may_change_it_and_only_by_a_click(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    for role in ("viewer", "member"):
        assert client.put(URL, headers={**HEADERS, "x-user-roles": role}, json=body()).status_code == 403
    for mode in ("typed", "voice"):
        refused = client.put(URL, headers=HEADERS, json=body(input_mode=mode))
        assert refused.status_code == 403 and "click" in refused.json()["error"]["message"]
    assert client.put(URL, headers=HEADERS, json={**body(), "extra": 1}).status_code == 422


@pytest.mark.parametrize(("field", "value"), [("transcript_days", 6), ("transcript_days", 1826), ("visitor_days", 29),
                                              ("visitor_days", 1826), ("transcript_days", 0), ("visitor_days", -5)])
def test_periods_outside_the_range_are_refused_with_a_clear_message(monkeypatch: pytest.MonkeyPatch, field: str, value: int) -> None:
    refused = TestClient(app).put(URL, headers=HEADERS, json=body(**{field: value}))
    assert refused.status_code == 422
    message = refused.json()["error"]["message"]
    assert ("Meeting transcripts" if field == "transcript_days" else "Visitor records") in message and "1825" in message


def test_refusals_are_answered_in_arabic(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    arabic = {**HEADERS, "accept-language": "ar"}
    assert client.put(URL, headers=arabic, json=body(visitor_days=5)).json()["error"]["message"] == \
        "يمكن الاحتفاظ بسجلات الزوار من 30 إلى 1825 يومًا."
    assert client.put(URL, headers=arabic, json=body(transcript_days=5)).json()["error"]["message"] == \
        "يمكن الاحتفاظ بنصوص الاجتماعات من 7 إلى 1825 يومًا."
    assert "بنقرة" in client.put(URL, headers=arabic, json=body(input_mode="typed")).json()["error"]["message"]


def _fake_loop(monkeypatch: pytest.MonkeyPatch, workspaces: list[tuple[str, str]], counts: dict[str, int]) -> list[str]:
    done: list[str] = []
    monkeypatch.setattr(rr, "discover", lambda factory, statement, params: [
        SimpleNamespace(tenant_id=t, workspace_id=w) for t, w in workspaces
        if (t, w) > (params["after_tenant"], params["after_workspace"])][: params["limit"]])

    @contextmanager
    def unit(factory, tenant_id, workspace_id, **_):  # type: ignore[no-untyped-def]
        yield SimpleNamespace(rollback=lambda: done.append(f"rollback:{workspace_id}"))
        done.append(f"commit:{workspace_id}")

    monkeypatch.setattr(rr, "scoped_unit", unit)
    monkeypatch.setattr(rr, "purge_workspace", lambda session, t, w, now: dict(counts))
    return done


def test_purge_pages_through_every_workspace_and_sums_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    done = _fake_loop(monkeypatch, [("t1", "w1"), ("t1", "w2"), ("t2", "w1")],
                      {"meetings": 1, "messages": 4, "events": 6, "visitors": 2})
    result = rr.purge_records(lambda: None, now=datetime(2026, 10, 28, tzinfo=timezone.utc), batch_size=2)  # type: ignore[arg-type,return-value]
    assert (result.workspaces_scanned, result.workspaces_purged) == (3, 3)
    assert (result.meeting_transcripts, result.transcript_messages, result.transcript_events, result.visitor_records) == (3, 12, 18, 6)
    assert done == ["commit:w1", "commit:w2", "commit:w1"] and result.stopped is False


def test_dry_run_rolls_every_workspace_back(monkeypatch: pytest.MonkeyPatch) -> None:
    done = _fake_loop(monkeypatch, [("t1", "w1")], {"meetings": 2, "messages": 1, "events": 1, "visitors": 0})
    result = rr.purge_records(lambda: None, dry_run=True)  # type: ignore[arg-type,return-value]
    assert result.dry_run and result.meeting_transcripts == 2 and done[0] == "rollback:w1"


def test_a_stop_request_finishes_the_workspace_in_progress_then_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    done = _fake_loop(monkeypatch, [("t1", "w1"), ("t1", "w2"), ("t1", "w3")],
                      {"meetings": 0, "messages": 0, "events": 0, "visitors": 1})
    asked = iter([False, True])
    result = rr.purge_records(lambda: None, should_stop=lambda: next(asked))  # type: ignore[arg-type,return-value]
    assert result.stopped and result.workspaces_scanned == 1 and done == ["commit:w1"]


def test_sigterm_during_the_purge_only_sets_the_stop_flag_and_the_old_handler_returns() -> None:
    previous = signal.getsignal(signal.SIGTERM)
    with rr.finish_on_sigterm() as stop:
        os.kill(os.getpid(), signal.SIGTERM)
        assert stop.is_set()
    assert signal.getsignal(signal.SIGTERM) == previous


def test_cli_refuses_without_postgres(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(settings, "repository_backend", "memory")
    assert rr.main(["--dry-run"]) == 2
    assert "postgresql" in capsys.readouterr().err
