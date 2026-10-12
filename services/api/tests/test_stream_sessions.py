"""Streaming responses must not hold the request's database session while they stream.

FastAPI (0.118+) ends a ``yield`` dependency with the default "request" scope only
after the whole response has been sent. For a server-sent event stream that is the
life of the browser tab, so every open tab held one pooled PostgreSQL connection
"idle in transaction". The pool (5 + 10 overflow) then ran dry after a short session
and every request, ``/health`` included, hung (the reported "web app freezes").
Streaming routes take the request repository with ``scope="function"``: the unit of
work commits and returns its connection before the first byte of the body.
"""

from __future__ import annotations

import typing
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse

from anum_api import audit_log
from anum_api import dependencies
from anum_api import main as api_main
from anum_api.main import app, store

HEADERS = {"x-tenant-id": "tenant_a", "x-workspace-id": "workspace_a", "x-user-id": "user_a", "x-user-roles": "owner,member"}


@pytest.fixture
def unit_of_work_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, int]]:
    """Counts request units of work that are open, wrapping the real (memory) one."""
    for collection in (store.tasks, store.runs, store.approvals, store.events, store.tenants, store.workspaces, store.memberships):
        collection.clear()
    state = {"open": 0}
    real = dependencies._unit_of_work

    @contextmanager
    def tracked(*args, **kwargs):  # type: ignore[no-untyped-def]
        with real(*args, **kwargs) as repository:
            state["open"] += 1
            try:
                yield repository
            finally:
                state["open"] -= 1

    monkeypatch.setattr(dependencies, "_unit_of_work", tracked)
    client = TestClient(app)
    assert client.post("/api/v1/tenants", headers=HEADERS, json={"name": "Tenant A"}).status_code == 201
    assert client.post("/api/v1/workspaces", headers=HEADERS, json={"name": "Workspace A"}).status_code == 201
    assert client.post("/api/v1/workspace-memberships/current", headers=HEADERS).status_code in {200, 201}
    yield state


SESSION_DEPENDENCIES = {
    dependencies.repository_context,
    dependencies.identity_repository_context,
    dependencies.memory_repository_context,
    dependencies.provisioning_repository_context,
}


def _routes(routes) -> Iterator[APIRoute]:  # type: ignore[no-untyped-def]
    # FastAPI 0.142+ keeps included routers nested (CLAUDE.md): walk them.
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        nested = getattr(getattr(route, "original_router", None), "routes", None)
        if nested:
            yield from _routes(nested)


def _session_dependencies(dependant):  # type: ignore[no-untyped-def]
    for sub in dependant.dependencies:
        if sub.call in SESSION_DEPENDENCIES:
            yield sub
        yield from _session_dependencies(sub)


def test_every_streaming_route_releases_its_request_session_before_the_body() -> None:
    streaming = []
    for route in _routes(app.router.routes):
        try:
            returns = typing.get_type_hints(route.endpoint).get("return")
        except Exception:  # an unresolvable annotation is not a streaming route
            continue
        if returns is StreamingResponse:
            streaming.append(route)
    paths = {route.path for route in streaming}
    assert any(path.endswith("/events/stream") for path in paths), paths
    assert any(path.endswith("/audit/export") for path in paths), paths
    held = [
        f"{route.path}: {sub.call.__name__}"
        for route in streaming
        for sub in _session_dependencies(route.dependant)
        if sub.scope != "function"
    ]
    assert not held, f"streaming routes holding a request session for the whole stream: {held}"


def test_event_stream_body_runs_after_the_request_session_is_released(
    unit_of_work_state: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []

    def poll(context, after=None):  # type: ignore[no-untyped-def]
        seen.append(unit_of_work_state["open"])
        return []

    monkeypatch.setattr(api_main, "list_events_for_stream", poll)
    response = TestClient(app).get("/api/v1/events/stream", headers=HEADERS, params={"follow": "false"})

    assert response.status_code == 200
    assert seen, "the stream body never polled"
    assert seen == [0] * len(seen), f"request sessions still open while streaming: {seen}"


def test_audit_export_body_runs_after_the_request_session_is_released(
    unit_of_work_state: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []

    def body(context, filters):  # type: ignore[no-untyped-def]
        seen.append(unit_of_work_state["open"])
        yield "[]"

    monkeypatch.setattr(audit_log, "_json_body", body)
    response = TestClient(app).get("/api/v1/audit/export", headers=HEADERS)

    assert response.status_code == 200, response.text
    assert seen == [0]
