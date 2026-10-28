"""Reception visitor register and the career building kind without a database (docs/ANUM_MINE_HQ_RECEPTION.md)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from anum_api import office_campus_api, office_starter_campus as sc, office_visitors_api as visitors
from anum_api.events import CanonicalEventName
from anum_api.main import app

HEADERS = {"x-tenant-id": "tenant_a", "x-workspace-id": "workspace_a", "x-user-id": "owner_a", "x-user-roles": "owner"}


def test_every_route_is_registered() -> None:
    paths = app.openapi()["paths"]
    assert set(paths["/api/v1/office/visitors"]) == {"get", "post"}
    assert set(paths["/api/v1/office/visitors/summary"]) == {"get"}
    assert set(paths["/api/v1/office/visitors/{visit_id}"]) == {"patch", "delete"}
    for move in ("check-in", "check-out", "cancel"):
        assert set(paths[f"/api/v1/office/visitors/{{visit_id}}/{move}"]) == {"post"}


def test_without_postgres_the_register_answers_503_never_fake_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api.settings import settings

    monkeypatch.setattr(settings, "repository_backend", "memory")
    client = TestClient(app)
    assert client.get("/api/v1/office/visitors", headers=HEADERS).status_code == 503
    assert client.get("/api/v1/office/visitors/summary", headers=HEADERS).status_code == 503
    body = {"visitor_name": "Example", "purpose": "Example", "expected_at": "2026-10-27T10:00:00+04:00", "input_mode": "click"}
    assert client.post("/api/v1/office/visitors", headers=HEADERS, json=body).status_code == 503


def test_a_viewer_is_refused_before_anything_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    body = {"visitor_name": "Example", "purpose": "Example", "expected_at": "2026-10-27T10:00:00+04:00", "input_mode": "click"}
    assert client.post("/api/v1/office/visitors", headers={**HEADERS, "x-user-roles": "viewer"}, json=body).status_code == 403


def test_text_is_cleaned_and_control_characters_refused() -> None:
    made = visitors.VisitCreate(visitor_name="  Visitor   Example ", purpose="Meeting", company=" ",
                                expected_at=datetime(2026, 10, 27, tzinfo=timezone.utc))
    assert made.visitor_name == "Visitor Example" and made.company is None and made.input_mode == "typed"
    with pytest.raises(ValidationError):
        visitors.VisitCreate(visitor_name="a\u0000b", purpose="x", expected_at=datetime(2026, 10, 27, tzinfo=timezone.utc))
    with pytest.raises(ValidationError):
        visitors.VisitCreate(visitor_name="x", purpose="x", expected_at=datetime(2026, 10, 27, tzinfo=timezone.utc), host_kind="robot")


def test_cursor_round_trip_and_refusal() -> None:
    when = datetime(2026, 10, 27, 9, 30, tzinfo=timezone.utc)
    assert visitors._decode_cursor(visitors._encode_cursor(when, "visit_1")) == (when, "visit_1")
    for bad in ("!!", "e30", visitors._encode_cursor(when, "x")[:-3]):
        with pytest.raises(HTTPException) as refused:
            visitors._decode_cursor(bad)
        assert refused.value.status_code == 422


def test_search_wildcards_are_escaped() -> None:
    assert visitors._like("100%_a") == "%100\\%\\_a%"


def test_only_click_confirms_a_write() -> None:
    visitors._click("click")
    for mode in ("typed", "voice"):
        with pytest.raises(HTTPException) as refused:
            visitors._click(mode)
        assert refused.value.status_code == 403


def test_visitor_event_name_and_career_kind() -> None:
    assert CanonicalEventName.OFFICE_VISITORS_CHANGED.value == "office.visitors_changed"
    assert "career" in office_campus_api.KINDS
    office_campus_api.ProfileBody(kind="career", icon="briefcase")


def test_starter_campus_buildings_carry_their_campus_kind() -> None:
    kinds = {b.key: b.campus_kind for b in sc.CAMPUS}
    assert kinds == {"hq": "executive", "commerce": "business", "tyre-pulse": "department", "corporate-services": "business",
                     "knowledge-ai": "business", "new-ventures": "business", "personal": "personal", "career": "career"}
    assert set(kinds.values()) <= set(office_campus_api.KINDS)
    plan = sc.plan_building(sc.BUILDING_BY_KEY["career"], sc.Facts(), "Asia/Dubai")
    assert plan["campus_kind"] == "career"
